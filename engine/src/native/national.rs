// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 李晓洋
//
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License as published by the Free
// Software Foundation, version 3.

use rayon::prelude::*;
use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering as AtomicOrdering};
use std::sync::{Arc, LazyLock};

/// Cooperative cancellation for the regional batch. The shared-cache builds
/// and the observer loop check this flag between work units so a desktop
/// cancel returns within seconds instead of waiting for the whole domain.
#[derive(Clone, Default)]
pub struct BatchCancel {
    flag: Option<Arc<AtomicBool>>,
    borrowed: Option<*const AtomicBool>,
}

impl BatchCancel {
    pub fn new(flag: Arc<AtomicBool>) -> Self {
        Self {
            flag: Some(flag),
            borrowed: None,
        }
    }

    /// Borrow an existing flag (e.g. the desktop runtime's cancel token).
    /// The caller must keep the flag alive for the whole batch call.
    ///
    /// # Safety contract
    /// The referenced `AtomicBool` must outlive every use of this value.
    pub fn from_ref(flag: &AtomicBool) -> Self {
        Self {
            flag: None,
            borrowed: Some(flag as *const AtomicBool),
        }
    }

    pub fn is_cancelled(&self) -> bool {
        if let Some(flag) = self.flag.as_ref() {
            return flag.load(AtomicOrdering::Relaxed);
        }
        match self.borrowed {
            // SAFETY: `from_ref` guarantees the flag outlives the batch;
            // AtomicBool::load is safe to call through a shared reference.
            Some(pointer) => unsafe { (*pointer).load(AtomicOrdering::Relaxed) },
            None => false,
        }
    }
}

// SAFETY: BatchCancel only performs atomic loads through the pointer; the
// referenced flag is required to outlive the batch (see `from_ref`).
unsafe impl Send for BatchCancel {}
unsafe impl Sync for BatchCancel {}

/// Layer count of the scalar solar trace record. The CPU-only work record
/// keeps the original contract layout for the CPU scoring path.
const SOLAR_RAY_LAYER_COUNT: usize = 3;
const SOLAR_RAY_WORK_FLAG_TARGET_TERRAIN_OVERRIDE: u32 = 1 << 0;
const SOLAR_RAY_WORK_FLAG_EXACT: u32 = 1 << 1;

/// One target-cell solar trace consumed by the authoritative scalar evaluator.
#[derive(Clone, Copy)]
struct SolarRayWork {
    left: u32,
    right: u32,
    active_mask: u32,
    flags: u32,

    weight: f32,
    left_seconds: f32,
    right_seconds: f32,
    target_lat: f32,
    target_lon: f32,
    target_distance_km: f32,
    target_sector_deg: f32,
    target_sector_cos: f32,
    solar_elevation_deg: f32,
    solar_illumination_elevation_rad: f32,
    solar_azimuth_deg: f32,
    target_terrain_override_m: f32,

    cloud_amount: [f32; SOLAR_RAY_LAYER_COUNT],
    cloud_opacity: [f32; SOLAR_RAY_LAYER_COUNT],
    cloud_material_quality: [f32; SOLAR_RAY_LAYER_COUNT],
    cloud_base_m: [f32; SOLAR_RAY_LAYER_COUNT],
    cloud_top_m: [f32; SOLAR_RAY_LAYER_COUNT],
    target_deep_risk: [f32; SOLAR_RAY_LAYER_COUNT],
}

impl SolarRayWork {
    fn active(&self, layer: usize) -> bool {
        layer < SOLAR_RAY_LAYER_COUNT && (self.active_mask & (1 << layer)) != 0
    }

    fn active_layers(&self) -> [bool; SOLAR_RAY_LAYER_COUNT] {
        std::array::from_fn(|layer| self.active(layer))
    }

    fn clouds(&self) -> [CloudLayerSample; SOLAR_RAY_LAYER_COUNT] {
        std::array::from_fn(|layer| CloudLayerSample {
            amount: self.cloud_amount[layer],
            opacity: self.cloud_opacity[layer],
            material_quality: self.cloud_material_quality[layer],
            base_m: self.cloud_base_m[layer],
            top_m: self.cloud_top_m[layer],
        })
    }

    fn target_terrain_override(&self) -> Option<f32> {
        (self.flags & SOLAR_RAY_WORK_FLAG_TARGET_TERRAIN_OVERRIDE != 0)
            .then_some(self.target_terrain_override_m)
    }
}

pub const GRID_FIELDS: usize = 36;
pub const AEROSOL_PROFILE_LEVELS: usize = 11;
pub const OBSERVER_FIELDS: usize = 3;
pub const SAMPLE_FIELDS: usize = 9;
pub const VIEW_DIAGNOSTIC_OFFSET: usize = 43;
// [0] forward visible cloud field, [1] forward burnable cloud field,
// [2] aerosol-dimmed burnable load, [3] pre-aerosol material load,
// [4] dominant burnable pressure-family code, [5..10] azimuthal layer amounts, [11..13]
// observer-cell visible/burnable/dimmed-load amounts, [14..16] ground-area
// diagnostics, [17] incoming blocking, [18] observer-cell material load,
// [19] sunlight-to-cloud aerosol load, [20] observer-cell sunlight load,
// [21..26] target-deck ray diagnostics used for regression analysis, [27..29]
// dominant-family burn-weighted cloud-base ASL, cloud-top ASL, and target-terrain
// ASL means, [30..33]
// observer-return aerosol optical depth/transmission for the complete field and observer column
// The primary field is the union of every GFS cloud cell
// projected into the complete sun-facing half of the observer's sky. It is a
// human-visible angular area, not a camera crop or ranked target measurement.
// Cloud base/top controls both the angular footprint and illumination.
pub const VIEW_DIAGNOSTIC_FIELDS: usize = 34;
// These fields are appended after the established observer-field diagnostics.
// They are a pre-veto ledger only; none is consumed by the score path.
pub const VETO_LEDGER_OFFSET: usize = VIEW_DIAGNOSTIC_OFFSET + VIEW_DIAGNOSTIC_FIELDS;
pub const VETO_LEDGER_FIELDS: usize = 5;
pub const VETO_CANDIDATE_OFFSET: usize = VETO_LEDGER_OFFSET;
pub const VETO_TARGET_ELIGIBLE_OFFSET: usize = VETO_LEDGER_OFFSET + 1;
pub const VETO_GEOMETRY_REACHABLE_OFFSET: usize = VETO_LEDGER_OFFSET + 2;
pub const VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET: usize = VETO_LEDGER_OFFSET + 3;
pub const VETO_UNOCCLUDED_BURN_UPPER_OFFSET: usize = VETO_LEDGER_OFFSET + 4;
pub const OUTPUT_FIELDS: usize = VETO_LEDGER_OFFSET + VETO_LEDGER_FIELDS;
pub const PATH_AUDIT_FIELDS: usize = 36;
pub const PATH_AUDIT_SCHEMA_VERSION: f32 = 2.0;
pub const PATH_AUDIT_SCHEMA_VERSION_OFFSET: usize = 0;
pub const PATH_AUDIT_EXTERNAL_TAU_OFFSET: usize = 1;
pub const PATH_AUDIT_TARGET_SELF_TAU_OFFSET: usize = 2;
pub const PATH_AUDIT_PRE_PEAK_EXTERNAL_FIELD_OFFSET: usize = 3;
pub const PATH_AUDIT_PEAK_FACTOR_OFFSET: usize = 4;
pub const PATH_AUDIT_TRACE_STEPS_MAX_OFFSET: usize = 5;
pub const PATH_AUDIT_UNIQUE_CELLS_MAX_OFFSET: usize = 6;
pub const PATH_AUDIT_REVISITED_CELLS_MAX_OFFSET: usize = 7;
pub const PATH_AUDIT_DOMAIN_BREAK_COUNT_OFFSET: usize = 8;
pub const PATH_AUDIT_RAY_HEIGHT_BREAK_COUNT_OFFSET: usize = 9;
pub const PATH_AUDIT_OPAQUE_CUTOFF_COUNT_OFFSET: usize = 10;
pub const PATH_AUDIT_FAMILY_TAU_HIGH_OFFSET: usize = 11;
pub const PATH_AUDIT_FAMILY_TAU_MID_OFFSET: usize = 12;
pub const PATH_AUDIT_FAMILY_TAU_LOW_OFFSET: usize = 13;
pub const PATH_AUDIT_TARGET_EXTENSION_COUNT_MAX_OFFSET: usize = 14;
pub const PATH_AUDIT_VISIBLE_WEIGHT_OFFSET: usize = 15;
pub const PATH_AUDIT_PB_LOSS_TOTAL_OFFSET: usize = 16;
pub const PATH_AUDIT_PB_DECOMPOSITION_ERROR_OFFSET: usize = 17;
pub const PATH_AUDIT_DOMINANT_LOSS_FIELD_OFFSET: usize = 18;
pub const PATH_AUDIT_DOMINANT_SHARE_OFFSET: usize = 19;
pub const PATH_AUDIT_DOMINANT_VALID_OFFSET: usize = 20;
pub const PATH_AUDIT_DOMINANT_TARGET_CELL_INDEX_OFFSET: usize = 21;
pub const PATH_AUDIT_DOMINANT_FAMILY_CODE_OFFSET: usize = 22;
pub const PATH_AUDIT_DOMINANT_DISTANCE_KM_OFFSET: usize = 23;
pub const PATH_AUDIT_DOMINANT_BEARING_DEG_OFFSET: usize = 24;
pub const PATH_AUDIT_DOMINANT_AMOUNT_OFFSET: usize = 25;
pub const PATH_AUDIT_DOMINANT_BASE_ASL_OFFSET: usize = 26;
pub const PATH_AUDIT_DOMINANT_TOP_ASL_OFFSET: usize = 27;
pub const PATH_AUDIT_DOMINANT_TERRAIN_ASL_OFFSET: usize = 28;
pub const PATH_AUDIT_DOMINANT_MATERIAL_QUALITY_OFFSET: usize = 29;
pub const PATH_AUDIT_DOMINANT_TARGET_OPACITY_OFFSET: usize = 30;
pub const PATH_AUDIT_DOMINANT_TARGET_DEEP_RISK_OFFSET: usize = 31;
pub const PATH_AUDIT_DOMINANT_EFFECTIVE_SELF_TRANSMISSION_OFFSET: usize = 32;
pub const PATH_AUDIT_DOMINANT_TARGET_DECK_CONNECTED_OFFSET: usize = 33;
pub const PATH_AUDIT_DOMINANT_TARGET_DECK_ATTENUATED_OFFSET: usize = 34;
pub const PATH_AUDIT_OMITTED_LOSS_FIELD_OFFSET: usize = 35;
pub const MAX_ANALYSIS_RADIUS_KM: f32 = 1000.0;
// Kept only for the legacy diagnostic ABI. National target eligibility no
// longer uses a universal distance cutoff.
pub const MIN_REMOTE_LOW_TARGET_DISTANCE_KM: f32 = 80.0;
// These records are emitted only by the offline selected-target ABI. They are
// deliberately not part of the national frame or score output.
pub const TARGET_AUDIT_FIELDS: usize = 28;
pub const TARGET_AUDIT_MAX_RECORDS: usize = 128;
pub const TARGET_AUDIT_SCHEMA_VERSION: f32 = 1.0;
pub const TARGET_AUDIT_SCHEMA_OFFSET: usize = 0;
pub const TARGET_AUDIT_VALID_OFFSET: usize = 1;
pub const TARGET_AUDIT_CELL_INDEX_OFFSET: usize = 2;
pub const TARGET_AUDIT_FAMILY_CODE_OFFSET: usize = 3;
pub const TARGET_AUDIT_DISTANCE_KM_OFFSET: usize = 4;
pub const TARGET_AUDIT_BEARING_DEG_OFFSET: usize = 5;
pub const TARGET_AUDIT_AMOUNT_OFFSET: usize = 6;
pub const TARGET_AUDIT_CANDIDATE_OVERLAP_FIELD_OFFSET: usize = 7;
pub const TARGET_AUDIT_VISIBLE_OVERLAP_FIELD_OFFSET: usize = 8;
pub const TARGET_AUDIT_BURN_CONTRIBUTION_FIELD_OFFSET: usize = 9;
pub const TARGET_AUDIT_TARGET_ELIGIBLE_OFFSET: usize = 10;
pub const TARGET_AUDIT_GEOMETRY_REACHABLE_OFFSET: usize = 11;
pub const TARGET_AUDIT_EXTERNAL_PATH_TRANSMISSION_OFFSET: usize = 12;
pub const TARGET_AUDIT_TARGET_SELF_TRANSMISSION_OFFSET: usize = 13;
pub const TARGET_AUDIT_EXTERNAL_TAU_OFFSET: usize = 14;
pub const TARGET_AUDIT_TARGET_SELF_TAU_OFFSET: usize = 15;
pub const TARGET_AUDIT_BASE_ASL_OFFSET: usize = 16;
pub const TARGET_AUDIT_TOP_ASL_OFFSET: usize = 17;
pub const TARGET_AUDIT_TERRAIN_ASL_OFFSET: usize = 18;
pub const TARGET_AUDIT_BASE_ABOVE_OBSERVER_OFFSET: usize = 19;
pub const TARGET_AUDIT_TOP_ABOVE_OBSERVER_OFFSET: usize = 20;
pub const TARGET_AUDIT_MATERIAL_QUALITY_OFFSET: usize = 21;
pub const TARGET_AUDIT_TARGET_OPACITY_OFFSET: usize = 22;
pub const TARGET_AUDIT_TARGET_DEEP_RISK_OFFSET: usize = 23;
pub const TARGET_AUDIT_DECK_CONNECTED_OFFSET: usize = 24;
pub const TARGET_AUDIT_DECK_ATTENUATED_OFFSET: usize = 25;
pub const TARGET_AUDIT_CLEAR_GAP_COUNT_OFFSET: usize = 26;
pub const TARGET_AUDIT_PATH_AUDIT_AVAILABLE_OFFSET: usize = 27;
const SAMPLE_VALID: usize = 6;
const SAMPLE_LEFT_SECONDS: usize = 7;
const SAMPLE_RIGHT_SECONDS: usize = 8;

const VIEW_AZIMUTH_BINS: usize = 36;
const VIEW_AZIMUTH_HALF_DEG: f32 = 90.0;
const VIEW_AZIMUTH_BIN_DEG: f32 = 5.0;
const FIELD_FORWARD_HALF_DEG: f32 = 90.0;
const CONTAINING_CELL_MAX_DISTANCE_PER_DEG_KM: f32 = 80.0;
// The strongest cloud patch proves that an azimuth can burn; the radial mean
// prevents one isolated range cell from representing the entire line of sight.
const VIEW_ELEVATION_BINS: usize = 90;
const VIEW_ANGULAR_CELLS: usize = VIEW_AZIMUTH_BINS * VIEW_ELEVATION_BINS;
static VIEW_ELEVATION_SOLID_ANGLE_WEIGHTS: LazyLock<[f32; VIEW_ELEVATION_BINS]> =
    LazyLock::new(|| {
        std::array::from_fn(|elevation_bin| {
            let lower = (elevation_bin as f32).to_radians();
            let upper = ((elevation_bin + 1) as f32).to_radians();
            upper.sin() - lower.sin()
        })
    });
const DOMINANT_BURNABLE_PRESSURE_FAMILY_MIN_AMOUNT: f32 = 0.005;
#[cfg(test)]
const VIEW_OPACITY_MAX: u16 = u8::MAX as u16;

const HIGH: usize = 0;
const MID: usize = 1;
const LOW: usize = 2;
const HIGH_TARGET_BODY_RISK: usize = 9;
const MID_TARGET_BODY_RISK: usize = 10;
const LOW_TARGET_BODY_RISK: usize = 11;
const HIGH_MATERIAL: usize = 4;
const BASE: usize = 5;
const TOP: usize = 6;
const PRESSURE: usize = 8;
const VISIBILITY: usize = 13;
const PRECIPITATION: usize = 14;
const MID_BLOCKER_OPACITY: usize = 15;
const LOW_BLOCKER_OPACITY: usize = 16;
const HIGH_BASE: usize = 17;
const HIGH_TOP: usize = 18;
const MID_BASE: usize = 19;
const MID_TOP: usize = 20;
const LOW_BASE: usize = 21;
const LOW_TOP: usize = 22;
const HIGH_BLOCKER_OPACITY: usize = 23;
const TERRAIN_ELEVATION: usize = 24;
const AEROSOL_AOD550: usize = 25;
const LOCAL_UPPER_SUPPORT: usize = 27;
const MID_MATERIAL: usize = 28;
const LOW_MATERIAL: usize = 29;
const HIGH_U: usize = 30;
const HIGH_V: usize = 31;
const MID_U: usize = 32;
const MID_V: usize = 33;
const LOW_U: usize = 34;
const LOW_V: usize = 35;

// The observer-field pass is a solar-facing half-plane, not a camera cone. The domain is the
// complete model cloud field in front of the observer. The field amount is
// an area-weighted fraction over every grid cell in the half-disk, including
// clear cells; this is deliberately independent of target ranking and camera
// solid angle.
// Keep a narrow uncertainty band around the squared-radius boundary. The
// final hypot comparison preserves the previous f32 inclusion semantics.
const RADIUS_SQUARED_PREFILTER_MARGIN: f32 = 1.0e-5;
const MAX_SELF_SHIELD_TARGET_THICKNESS_M: f32 = 6_500.0;
const MAX_LAYERED_SELF_SHIELD_TARGET_AMOUNT: f32 = 0.75;
// A cloud deck that blocks more than roughly 40% of the direct beam is no
// longer a transparent target sharing its own illumination path.  Keeping
// this threshold below the old 0.80 value prevents dense high-cloud decks
// from being counted as burnable cirrus merely because they are vertically
// shallow in the pressure profile.
const MAX_SELF_SHIELD_TARGET_OPACITY: f32 = 0.60;
const MAX_SELF_SHIELD_TARGET_DEEP_RISK: f32 = 0.20;
const FULL_DEEP_TARGET_TRANSMISSION_RISK: f32 = 0.58;
const MIN_DEEP_TARGET_TRANSMISSION_OPACITY: f32 = 0.60;
const HIGH_CLOUD_MAX_PRESSURE_HPA: f32 = 400.0;
const MID_CLOUD_MAX_PRESSURE_HPA: f32 = 700.0;
const MIN_CONNECTED_TARGET_DECK_AMOUNT: f32 = 0.10;
// The observer return leg is a contrast-response proxy, not a second direct
// beam. Its exponent is empirical and must remain explicitly separated from
// the raw Beer-Lambert transmission emitted as a radiometric diagnostic.
const OBSERVER_CONTRAST_PERCEPTUAL_EXPONENT: f32 = 0.025;
const SOLAR_SEMI_DIAMETER_DEG: f32 = 0.2666667;
const EARTH_RADIUS_KM: f32 = 6371.0;

const AEROSOL_PROFILE_PRESSURE_HPA: [f32; AEROSOL_PROFILE_LEVELS] = [
    1000.0, 950.0, 925.0, 900.0, 850.0, 800.0, 700.0, 600.0, 500.0, 400.0, 300.0,
];
static AEROSOL_PROFILE_HEIGHTS_M: LazyLock<[f32; AEROSOL_PROFILE_LEVELS]> = LazyLock::new(|| {
    AEROSOL_PROFILE_PRESSURE_HPA
        .map(|pressure_hpa| 44_330.0 * (1.0 - (pressure_hpa / 1013.25).powf(0.1903)))
});

struct SphericalPathGeometry {
    observer_elevation_m: f32,
    target_elevation_m: f32,
    target_distance_km: f32,
    observer_radius_m: f32,
    target_radius_m: f32,
    sin_target: f32,
    cos_target: f32,
}

impl SphericalPathGeometry {
    fn new(observer_elevation_m: f32, target_elevation_m: f32, target_distance_km: f32) -> Self {
        let target_distance_km = target_distance_km.max(0.0);
        let target_angle = target_distance_km / EARTH_RADIUS_KM;
        let (sin_target, cos_target) = target_angle.sin_cos();
        Self::with_target_trig(
            observer_elevation_m,
            target_elevation_m,
            target_distance_km,
            sin_target,
            cos_target,
        )
    }

    fn with_target_trig(
        observer_elevation_m: f32,
        target_elevation_m: f32,
        target_distance_km: f32,
        sin_target: f32,
        cos_target: f32,
    ) -> Self {
        let radius_m = EARTH_RADIUS_KM * 1000.0;
        Self {
            observer_elevation_m,
            target_elevation_m,
            target_distance_km,
            observer_radius_m: radius_m + observer_elevation_m,
            target_radius_m: radius_m + target_elevation_m,
            sin_target,
            cos_target,
        }
    }

    fn height_m(&self, sample_distance_km: f32) -> f32 {
        let target_distance = self.target_distance_km;
        let sample_distance = sample_distance_km.clamp(0.0, target_distance);
        let sample_tangent = (sample_distance / EARTH_RADIUS_KM).tan();
        self.height_m_with_tangent(sample_distance, sample_tangent)
    }

    fn height_m_with_tangent(&self, sample_distance_km: f32, sample_tangent: f32) -> f32 {
        let target_distance = self.target_distance_km;
        let sample_distance = sample_distance_km.clamp(0.0, target_distance);
        let observer_elevation_m = self.observer_elevation_m;
        let target_elevation_m = self.target_elevation_m;
        if target_distance <= 1e-6 || sample_distance <= 1e-6 {
            return observer_elevation_m;
        }
        if sample_distance >= target_distance - 1e-6 {
            return target_elevation_m;
        }

        let radius_m = EARTH_RADIUS_KM * 1000.0;
        let denominator = self.target_radius_m * self.sin_target
            - sample_tangent * (self.target_radius_m * self.cos_target - self.observer_radius_m);
        if denominator.abs() <= 1e-6 {
            let fraction = sample_distance / target_distance;
            return observer_elevation_m + fraction * (target_elevation_m - observer_elevation_m);
        }
        let fraction = (sample_tangent * self.observer_radius_m / denominator).clamp(0.0, 1.0);
        let x = self.observer_radius_m
            + fraction * (self.target_radius_m * self.cos_target - self.observer_radius_m);
        let y = fraction * self.target_radius_m * self.sin_target;
        (x.mul_add(x, y * y).sqrt() - radius_m).max(-radius_m)
    }
}

fn spherical_line_of_sight_height_m(
    observer_elevation_m: f32,
    target_elevation_m: f32,
    target_distance_km: f32,
    sample_distance_km: f32,
) -> f32 {
    SphericalPathGeometry::new(observer_elevation_m, target_elevation_m, target_distance_km)
        .height_m(sample_distance_km)
}
fn clamp01(value: f32) -> f32 {
    value.clamp(0.0, 1.0)
}

fn solar_refraction_deg(geometric_elevation_deg: f32) -> f32 {
    if !geometric_elevation_deg.is_finite() || geometric_elevation_deg >= 85.0 {
        return 0.0;
    }
    let tangent = geometric_elevation_deg.to_radians().tan();
    if tangent.abs() < 1e-6 {
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

fn solar_illumination_elevation_deg(geometric_elevation_deg: f32) -> f32 {
    geometric_elevation_deg
        + solar_refraction_deg(geometric_elevation_deg)
        + SOLAR_SEMI_DIAMETER_DEG
}

fn target_deck_can_share_self_shielding(thickness_m: f32, opacity: f32, deep_risk: f32) -> bool {
    deep_risk < MAX_SELF_SHIELD_TARGET_DEEP_RISK
        // A thin but optically dense deck is still a sun-facing wall. The
        // former thickness-only branch let high-opacity cirrostratus share
        // the target's self-shielding exemption and produced false bright
        // events in otherwise continuous high-cloud fields.
        && opacity <= MAX_SELF_SHIELD_TARGET_OPACITY
        && thickness_m <= MAX_SELF_SHIELD_TARGET_THICKNESS_M
}

/// The target deck's self-shielding exemption describes the deck the observer
/// is actually looking at. Same-layer cells far from the target are just the
/// surrounding cloud field and must block like any other deck; the exemption
/// ramps back to full blocking between NEAR and FAR so that one extra grid
/// cell cannot flip the target's transmission. Distances are physical
/// kilometers, independent of the path step size.
const TARGET_SELF_SHIELD_NEAR_KM: f32 = 60.0;
const TARGET_SELF_SHIELD_FAR_KM: f32 = 150.0;

#[inline(always)]
fn target_extension_factor_at_distance(distance_km: f32) -> f32 {
    if !distance_km.is_finite() || distance_km >= TARGET_SELF_SHIELD_FAR_KM {
        1.0
    } else if distance_km <= TARGET_SELF_SHIELD_NEAR_KM {
        0.20
    } else {
        0.20 + 0.80 * (distance_km - TARGET_SELF_SHIELD_NEAR_KM)
            / (TARGET_SELF_SHIELD_FAR_KM - TARGET_SELF_SHIELD_NEAR_KM)
    }
}

/// A single below-threshold grid cell no longer tears the target deck apart:
/// the connection latch requires this many consecutive disconnected cells
/// before the deck counts as broken. One marginal cell in an otherwise
/// continuous deck used to permanently disable self-shielding and jump the
/// burn transmission by a factor of ~2.6.
const TARGET_DECK_BREAK_RUN_CELLS: u32 = 3;

/// A hard-clear cell is a real hole in the deck, not bilinear sampling noise:
/// it breaks the connection immediately. Marginal dips below the connected
/// threshold must persist for TARGET_DECK_BREAK_RUN_CELLS consecutive cells
/// before the deck counts as broken.
const TARGET_DECK_HARD_CLEAR_AMOUNT: f32 = 0.02;

#[inline(always)]
fn target_layer_self_shielding_needs_adjacent(
    target_layer: usize,
    target_thickness_m: f32,
    target_amount: f32,
) -> bool {
    target_layer < 2
        && target_thickness_m <= MAX_SELF_SHIELD_TARGET_THICKNESS_M
        && target_amount < MAX_LAYERED_SELF_SHIELD_TARGET_AMOUNT
}

fn target_layer_self_shielding_risk(
    target_layer: usize,
    target_deep_risk: f32,
    target_base_m: f32,
    target_top_m: f32,
    target_thickness_m: f32,
    target_amount: f32,
    adjacent_layer: CloudLayerSample,
) -> f32 {
    if target_layer_self_shielding_needs_adjacent(target_layer, target_thickness_m, target_amount)
        && adjacent_layer.amount < MAX_LAYERED_SELF_SHIELD_TARGET_AMOUNT
        && target_deck_extension_is_connected(adjacent_layer, target_base_m, target_top_m)
    {
        0.0
    } else {
        clamp01(target_deep_risk)
    }
}

fn deep_target_cloud_self_transmission(opacity: f32, deep_risk: f32) -> f32 {
    let opacity = clamp01(opacity);
    let deep_risk = clamp01(deep_risk);
    if opacity < MIN_DEEP_TARGET_TRANSMISSION_OPACITY
        || deep_risk <= MAX_SELF_SHIELD_TARGET_DEEP_RISK
    {
        return 1.0;
    }
    // ``opacity`` is the normalized optical-depth proxy of the cloudy part.
    // ``deep_risk`` is the fraction of the unresolved vertical cloud body that
    // the target ray is expected to cross. Combining them as
    // tau_target = -ln(1-opacity) * deep_risk keeps the target-body term in
    // Beer-Lambert space, makes deeper clouds strictly less transmissive, and
    // avoids treating the score's material quality as a second optical input.
    let risk_position = ((deep_risk - MAX_SELF_SHIELD_TARGET_DEEP_RISK)
        / (FULL_DEEP_TARGET_TRANSMISSION_RISK - MAX_SELF_SHIELD_TARGET_DEEP_RISK))
        .clamp(0.0, 1.0);
    let unresolved_path_fraction = risk_position * risk_position * (3.0 - 2.0 * risk_position);
    let one_pass_transmission = (1.0 - opacity).clamp(0.02, 1.0);
    let target_optical_depth = -one_pass_transmission.ln() * unresolved_path_fraction;
    (-target_optical_depth).exp().clamp(0.02, 1.0)
}

fn target_deck_extension_is_connected(
    extension: CloudLayerSample,
    cloud_base_m: f32,
    cloud_top_m: f32,
) -> bool {
    if extension.amount < MIN_CONNECTED_TARGET_DECK_AMOUNT {
        return false;
    }
    let overlaps =
        extension.top_m >= cloud_base_m - 900.0 && extension.base_m <= cloud_top_m + 900.0;
    overlaps
}

fn cloud_blocking_factor(
    cloud: CloudLayerSample,
    intersection: f32,
    target_extension_factor: f32,
) -> f32 {
    // Cloud amount is the resolved horizontal coverage of the model cell;
    // opacity describes the cloudy part's optical depth. Combine each once
    // so a fractional cloud field is not treated as a solid wall and is not
    // attenuated twice by the Python preprocessing layer.
    cloud_blocking_factor_values(
        cloud.amount,
        cloud.opacity,
        intersection,
        target_extension_factor,
    )
}

#[inline(always)]
fn cloud_blocking_factor_values(
    amount: f32,
    opacity: f32,
    intersection: f32,
    target_extension_factor: f32,
) -> f32 {
    clamp01(amount * opacity * clamp01(intersection) * clamp01(target_extension_factor))
}

fn add_target_self_optical_depth(cloud_optical_depth: f32, target_self_transmission: f32) -> f32 {
    if !cloud_optical_depth.is_finite() || !target_self_transmission.is_finite() {
        return cloud_optical_depth.max(0.0);
    }
    if target_self_transmission >= 1.0 {
        return cloud_optical_depth.max(0.0);
    }
    cloud_optical_depth.max(0.0) + (-target_self_transmission.clamp(0.02, 1.0).ln())
}

const PEAK_BLOCKING_TRANSMISSION_WEIGHT: f32 = 0.72;
const SOLAR_PATH_TEMPLATE_BLOCK_SIZE: usize = 8;
const SOLAR_PATH_TEMPLATE_ANGLE_BIN_DEG: f32 = 1.0;
const SOLAR_PATH_TEMPLATE_TIME_SLICE_SECONDS: f64 = 60.0;
const SOLAR_PATH_TEMPLATE_WEIGHT_BIN: f32 = 0.02;
const SOLAR_PATH_TEMPLATE_MAX_ENTRIES: usize = 8192;
const SOLAR_PATH_EXACT_CACHE_MAX_ENTRIES: usize = 4096;
const OBSERVER_PARALLEL_MIN_LEN: usize = 16;
const SHARED_SOLAR_PATH_SAMPLE_MIN_OBSERVERS: usize = 2048;
const SHARED_SOLAR_PATH_MULTI_SAMPLE_MIN_OBSERVERS: usize = 2048;

#[cfg(target_arch = "aarch64")]
static SOLAR_BLOCKING_NEON_ENABLED: LazyLock<bool> =
    LazyLock::new(|| std::env::var_os("FIRESKY_NATIVE_DISABLE_NEON_BLOCKING").is_none());
static MULTI_BEARING_CLOUD_CACHE_ENABLED: LazyLock<bool> =
    LazyLock::new(|| std::env::var_os("FIRESKY_NATIVE_ENABLE_MULTI_BEARING_CLOUD_CACHE").is_some());

pub(crate) fn cloud_transmission_from_path(cloud_optical_depth: f32, peak_blocking: f32) -> f32 {
    if !cloud_optical_depth.is_finite() || !peak_blocking.is_finite() {
        return 0.0;
    }
    // The integrated tau is built from coarse cloud-cell path samples. Keep a
    // separate peak correction for the unresolved densest segment; it is an
    // empirical sub-grid correction, not a second physical optical-depth
    // measurement. The no-double candidate was rejected by cross-day replay.
    let peak_factor = 1.0 - PEAK_BLOCKING_TRANSMISSION_WEIGHT * clamp01(peak_blocking);
    clamp01((-cloud_optical_depth.max(0.0)).exp() * peak_factor.max(0.0))
}

#[inline(always)]
pub(crate) fn cloud_optical_depth_increment(blocking: f32, step_km: f32) -> f32 {
    if blocking == 0.0 {
        0.0
    } else if blocking >= 1.0 {
        step_km / 140.0
    } else {
        blocking.powf(1.35) * step_km / 140.0
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct AerosolColumn {
    aod550: f32,
    extinction_m_inv: [f32; AEROSOL_PROFILE_LEVELS],
}

#[derive(Clone, Copy, Default)]
struct AerosolPathCache {
    column: AerosolColumn,
    normalization: f32,
    support: Option<(f32, f32)>,
    valid: bool,
}

impl AerosolColumn {
    fn is_valid(&self) -> bool {
        self.aod550.is_finite()
            && (0.0..=10.0).contains(&self.aod550)
            && self
                .extinction_m_inv
                .iter()
                .all(|value| value.is_finite() && *value >= 0.0)
    }

    fn support(terrain_asl_m: f32) -> Option<(f32, f32)> {
        if !terrain_asl_m.is_finite() {
            return None;
        }
        let lower = terrain_asl_m.max(AEROSOL_PROFILE_HEIGHTS_M[0]);
        let upper = AEROSOL_PROFILE_HEIGHTS_M[AEROSOL_PROFILE_LEVELS - 1];
        (upper > lower).then_some((lower, upper))
    }

    fn raw_value_at_height(&self, height_asl_m: f32) -> f32 {
        if !height_asl_m.is_finite()
            || height_asl_m < AEROSOL_PROFILE_HEIGHTS_M[0]
            || height_asl_m > AEROSOL_PROFILE_HEIGHTS_M[AEROSOL_PROFILE_LEVELS - 1]
        {
            return 0.0;
        }
        let heights = &*AEROSOL_PROFILE_HEIGHTS_M;
        // The profile has a fixed eleven-level layout. Keep the same first
        // upper-bound match and interpolation expression as the old loop, but
        // avoid restarting a loop for every path height.
        let level = if height_asl_m <= heights[1] {
            0
        } else if height_asl_m <= heights[2] {
            1
        } else if height_asl_m <= heights[3] {
            2
        } else if height_asl_m <= heights[4] {
            3
        } else if height_asl_m <= heights[5] {
            4
        } else if height_asl_m <= heights[6] {
            5
        } else if height_asl_m <= heights[7] {
            6
        } else if height_asl_m <= heights[8] {
            7
        } else if height_asl_m <= heights[9] {
            8
        } else {
            9
        };
        let lower_height = heights[level];
        let upper_height = heights[level + 1];
        let weight =
            ((height_asl_m - lower_height) / (upper_height - lower_height)).clamp(0.0, 1.0);
        self.extinction_m_inv[level]
            + weight * (self.extinction_m_inv[level + 1] - self.extinction_m_inv[level])
    }

    fn raw_integral(&self, lower_asl_m: f32, upper_asl_m: f32) -> f32 {
        if !lower_asl_m.is_finite() || !upper_asl_m.is_finite() || upper_asl_m <= lower_asl_m {
            return 0.0;
        }
        let heights = &*AEROSOL_PROFILE_HEIGHTS_M;
        let lower = lower_asl_m.max(AEROSOL_PROFILE_HEIGHTS_M[0]);
        let upper = upper_asl_m.min(AEROSOL_PROFILE_HEIGHTS_M[AEROSOL_PROFILE_LEVELS - 1]);
        if upper <= lower {
            return 0.0;
        }

        // Normalization is requested for the complete profile for the vast
        // majority of lowland path nodes. Keep the original trapezoid
        // operation order, but remove the per-level boundary tests and the
        // initial profile lookup from that hot case.
        if lower == heights[0] && upper == heights[AEROSOL_PROFILE_LEVELS - 1] {
            let mut integral = 0.0f32;
            let mut lower_value = self.extinction_m_inv[0]
                + 0.0 * (self.extinction_m_inv[1] - self.extinction_m_inv[0]);
            for level in 0..AEROSOL_PROFILE_LEVELS - 1 {
                let level_lower = heights[level];
                let level_upper = heights[level + 1];
                let upper_value = self.extinction_m_inv[level]
                    + 1.0 * (self.extinction_m_inv[level + 1] - self.extinction_m_inv[level]);
                integral += 0.5 * (lower_value + upper_value) * (level_upper - level_lower);
                lower_value = upper_value;
            }
            return integral;
        }
        let mut integral = 0.0f32;
        let mut segment_lower = lower;
        let mut lower_value = self.raw_value_at_height(segment_lower);
        for level in 0..AEROSOL_PROFILE_LEVELS - 1 {
            let level_lower = heights[level];
            let level_upper = heights[level + 1];
            if level_upper <= segment_lower {
                continue;
            }
            if segment_lower >= upper {
                break;
            }
            let segment_upper = level_upper.min(upper);
            let upper_weight =
                ((segment_upper - level_lower) / (level_upper - level_lower)).clamp(0.0, 1.0);
            let upper_value = self.extinction_m_inv[level]
                + upper_weight * (self.extinction_m_inv[level + 1] - self.extinction_m_inv[level]);
            integral += 0.5 * (lower_value + upper_value) * (segment_upper - segment_lower);
            segment_lower = segment_upper;
            lower_value = upper_value;
        }
        integral
    }

    fn normalization_integral(&self, terrain_asl_m: f32) -> f32 {
        // Normalize over the complete standard-atmosphere profile, not the
        // terrain-clipped support: a high-terrain cell would otherwise smear
        // the full column AOD — including the boundary-layer share that lives
        // below its terrain — into the thin band above the ground and inflate
        // extinction several-fold. The support still clips which heights are
        // sampled, so the underground share is simply discarded instead of
        // being redistributed onto the remaining column.
        let _ = terrain_asl_m;
        self.raw_integral(
            AEROSOL_PROFILE_HEIGHTS_M[0],
            AEROSOL_PROFILE_HEIGHTS_M[AEROSOL_PROFILE_LEVELS - 1],
        )
    }

    #[cfg(test)]
    fn extinction_km_inv(&self, height_asl_m: f32, terrain_asl_m: f32) -> f32 {
        if !self.is_valid() || self.aod550 == 0.0 {
            return 0.0;
        }
        let normalization = self.normalization_integral(terrain_asl_m);
        self.extinction_km_inv_with_normalization(height_asl_m, terrain_asl_m, normalization)
    }

    #[cfg(test)]
    fn extinction_km_inv_with_normalization(
        &self,
        height_asl_m: f32,
        terrain_asl_m: f32,
        normalization: f32,
    ) -> f32 {
        if !self.is_valid() || self.aod550 == 0.0 {
            return 0.0;
        }
        let Some((lower, upper)) = Self::support(terrain_asl_m) else {
            return 0.0;
        };
        if height_asl_m < lower || height_asl_m > upper {
            return 0.0;
        }
        if !normalization.is_finite() || normalization <= 0.0 {
            return 0.0;
        }
        self.aod550 * self.raw_value_at_height(height_asl_m) / normalization * 1000.0
    }

    fn extinction_km_inv_from_path_cache(&self, height_asl_m: f32, cache: AerosolPathCache) -> f32 {
        if !cache.valid || cache.column.aod550 == 0.0 {
            return 0.0;
        }
        let Some((lower, upper)) = cache.support else {
            return 0.0;
        };
        if !height_asl_m.is_finite() || height_asl_m < lower || height_asl_m > upper {
            return 0.0;
        }
        if !cache.normalization.is_finite() || cache.normalization <= 0.0 {
            return 0.0;
        }
        cache.column.aod550 * cache.column.raw_value_at_height(height_asl_m) / cache.normalization
            * 1000.0
    }

    #[cfg(test)]
    fn vertical_optical_depth(
        &self,
        lower_height_asl_m: f32,
        upper_height_asl_m: f32,
        terrain_asl_m: f32,
    ) -> f32 {
        if !self.is_valid()
            || self.aod550 == 0.0
            || !lower_height_asl_m.is_finite()
            || !upper_height_asl_m.is_finite()
            || upper_height_asl_m <= lower_height_asl_m
        {
            return 0.0;
        }
        if Self::support(terrain_asl_m).is_none() {
            return 0.0;
        }
        // Same full-profile normalization as `normalization_integral`: the
        // segment's AOD is its share of the complete column, so a high-terrain
        // station does not inherit the boundary-layer AOD it cannot see.
        let normalization = self.raw_integral(
            AEROSOL_PROFILE_HEIGHTS_M[0],
            AEROSOL_PROFILE_HEIGHTS_M[AEROSOL_PROFILE_LEVELS - 1],
        );
        self.vertical_optical_depth_with_normalization(
            lower_height_asl_m,
            upper_height_asl_m,
            terrain_asl_m,
            normalization,
        )
    }

    fn vertical_optical_depth_with_normalization(
        &self,
        lower_height_asl_m: f32,
        upper_height_asl_m: f32,
        terrain_asl_m: f32,
        normalization: f32,
    ) -> f32 {
        if !self.is_valid()
            || self.aod550 == 0.0
            || !lower_height_asl_m.is_finite()
            || !upper_height_asl_m.is_finite()
            || upper_height_asl_m <= lower_height_asl_m
        {
            return 0.0;
        }
        let Some((support_lower, support_upper)) = Self::support(terrain_asl_m) else {
            return 0.0;
        };
        if !normalization.is_finite() || normalization <= 0.0 {
            return 0.0;
        }
        let path_integral = self.raw_integral(
            lower_height_asl_m.max(support_lower),
            upper_height_asl_m.min(support_upper),
        );
        (self.aod550 * path_integral / normalization).max(0.0)
    }
}

#[derive(Clone, Copy, Debug)]
struct HorizontalPathNode {
    distance_km: f32,
    sample_tangent: f32,
    cell: Option<usize>,
    horizontal: [(usize, f32); 4],
}

#[derive(Clone, Copy)]
struct SharedHorizontalPathNode {
    cell: u32,
    horizontal: [(u32, f32); 4],
}

#[derive(Clone, Copy, Default)]
struct SharedCloudLayerSample {
    amount: f32,
    opacity: f32,
    base_m: f32,
    top_m: f32,
}

impl SharedCloudLayerSample {
    fn from_cloud(cloud: CloudLayerSample) -> Self {
        Self {
            amount: cloud.amount,
            opacity: cloud.opacity,
            base_m: cloud.base_m,
            top_m: cloud.top_m,
        }
    }

    fn to_cloud(self) -> CloudLayerSample {
        CloudLayerSample {
            amount: self.amount,
            opacity: self.opacity,
            material_quality: 0.0,
            base_m: self.base_m,
            top_m: self.top_m,
        }
    }
}

impl SharedHorizontalPathNode {
    fn from_node(node: HorizontalPathNode) -> Self {
        Self {
            cell: node.cell.map_or(u32::MAX, |cell| cell as u32),
            horizontal: node.horizontal.map(|(cell, weight)| (cell as u32, weight)),
        }
    }

    fn to_node(self) -> HorizontalPathNode {
        HorizontalPathNode {
            distance_km: 0.0,
            sample_tangent: 0.0,
            cell: (self.cell != u32::MAX).then_some(self.cell as usize),
            horizontal: self
                .horizontal
                .map(|(cell, weight)| (cell as usize, weight)),
        }
    }
}

struct SharedSolarPathNodes {
    bearing_bits: u32,
    nodes_per_cell: usize,
    distances_km: Vec<f32>,
    sample_tangents: Vec<f32>,
    nodes: Vec<SharedHorizontalPathNode>,
}

impl SharedSolarPathNodes {
    #[inline(always)]
    fn node(&self, cell: usize, index: usize) -> Option<HorizontalPathNode> {
        if index >= self.nodes_per_cell {
            return None;
        }
        self.nodes
            .get(cell.checked_mul(self.nodes_per_cell)?.checked_add(index)?)
            .copied()
            .map(|node| {
                let mut node = node.to_node();
                node.distance_km = self.distances_km[index];
                node.sample_tangent = self.sample_tangents[index];
                node
            })
    }
}

struct SharedSolarPathNodeSets {
    paths: Vec<SharedSolarPathNodes>,
}

#[derive(Clone, Copy)]
struct PathDistanceTrig {
    distance_bits: u32,
    sin: f32,
    cos: f32,
    tangent: f32,
}

fn shared_solar_path_distance_table(
    step_km: f32,
    max_trace_km: f32,
    nodes_per_cell: usize,
) -> Vec<(f32, PathDistanceTrig)> {
    (0..nodes_per_cell)
        .map(|index| {
            let distance_km = if index == 0 {
                0.0
            } else {
                (index as f32 * step_km).min(max_trace_km)
            };
            let angular_distance = distance_km / EARTH_RADIUS_KM;
            let (sin, cos) = angular_distance.sin_cos();
            (
                distance_km,
                PathDistanceTrig {
                    distance_bits: distance_km.to_bits(),
                    sin,
                    cos,
                    tangent: sin / cos,
                },
            )
        })
        .collect()
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct SolarPathTemplateKey {
    left: usize,
    right: usize,
    time_slice: i64,
    weight_bucket: i32,
    target_block: usize,
    bearing_bucket: i32,
    exact_target_cell: usize,
    exact_bearing_bits: u32,
    exact_weight_bits: u32,
    exact_left_seconds_bits: u32,
    exact_right_seconds_bits: u32,
    exact: bool,
}

struct SolarPathTemplate {
    nodes: Vec<HorizontalPathNode>,
    cloud_layers: Vec<[CloudLayerSample; 3]>,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct LayerCacheKey {
    left: usize,
    right: usize,
    weight_bits: u32,
    left_seconds_bits: u32,
    right_seconds_bits: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TemporalCacheMode {
    Quantized120,
    Consistent120,
    Exact,
}

static TEMPORAL_CACHE_MODE: LazyLock<TemporalCacheMode> = LazyLock::new(|| {
    if std::env::var_os("FIRESKY_NATIVE_EXACT_TEMPORAL_KEYS").is_some() {
        TemporalCacheMode::Exact
    } else if std::env::var_os("FIRESKY_NATIVE_CONSISTENT_TEMPORAL_KEYS").is_some() {
        TemporalCacheMode::Consistent120
    } else {
        TemporalCacheMode::Quantized120
    }
});

/// Temporal cache key quantization. Raw f32 bits of weight/seconds differ
/// per observer even inside one model bracket, exploding the shared cache
/// key count. Bucketing to 120 s (weight included, expressed through the
/// bracket span) bounds each temporal input's rounding error to 60 seconds.
/// This is an approximation, not bitwise reuse of the raw temporal inputs.
/// `weight_bits` always stores a dimensionless 0..1 weight: the bucketed
/// seconds are divided back by the bracket span before reconstruction.
///
/// `Quantized120` rounds weight, left seconds and right seconds independently,
/// so the reconstructed seconds need not sum to the bracket span (60/180 s
/// becomes 0/240 s while the weight becomes 120/3600). `Consistent120` instead
/// rounds the elapsed time from the bracket start once and derives weight,
/// left seconds and right seconds from that single value: the bracket sum and
/// endpoints are preserved exactly and one bucket index drives every temporal
/// input. It remains an explicit approximation (up to 60 s per side), selected
/// with FIRESKY_NATIVE_CONSISTENT_TEMPORAL_KEYS; the production default is
/// unchanged until A/B evidence against the exact mode is recorded.
fn temporal_cache_key(
    left: usize,
    right: usize,
    weight: f32,
    left_seconds: f32,
    right_seconds: f32,
    mode: TemporalCacheMode,
) -> LayerCacheKey {
    if mode == TemporalCacheMode::Exact {
        return LayerCacheKey {
            left,
            right,
            weight_bits: weight.to_bits(),
            left_seconds_bits: left_seconds.to_bits(),
            right_seconds_bits: right_seconds.to_bits(),
        };
    }
    let bucket = |value: f32| (value / 120.0).round_ties_even() * 120.0;
    // Weight advances one unit per bracket; GFS steps are hourly, so express
    // it in seconds (3600 per frame index) to align with the seconds buckets.
    let span = if right > left {
        ((right - left) * 3600) as f32
    } else {
        3600.0
    };
    if mode == TemporalCacheMode::Consistent120 {
        // Round the elapsed time from the bracket start once. The weight and
        // both remaining-second sides are derived from that single bucket so
        // left + right == span holds exactly for every reconstructed input.
        let elapsed = (weight * span).clamp(0.0, span);
        let bucketed = bucket(elapsed);
        return LayerCacheKey {
            left,
            right,
            weight_bits: (bucketed / span).to_bits(),
            left_seconds_bits: bucketed.to_bits(),
            right_seconds_bits: (span - bucketed).to_bits(),
        };
    }
    LayerCacheKey {
        left,
        right,
        weight_bits: (bucket(weight * span) / span).to_bits(),
        left_seconds_bits: bucket(left_seconds).to_bits(),
        right_seconds_bits: bucket(right_seconds).to_bits(),
    }
}

/// All generation-scoped observer caches must use the same temporal inputs
/// as the shared-motion key. Mixing raw and bucketed values makes a result
/// depend on which sample in a bucket happened to populate the cache first.
fn temporal_cache_inputs(key: LayerCacheKey) -> (f32, f32, f32) {
    (
        f32::from_bits(key.weight_bits),
        f32::from_bits(key.left_seconds_bits),
        f32::from_bits(key.right_seconds_bits),
    )
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct SolarPathCloudCacheKey {
    layer: LayerCacheKey,
    target_cell: usize,
    bearing_bits: u32,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct WindCacheKey {
    left: usize,
    right: usize,
    left_enabled: bool,
    right_enabled: bool,
}

struct SharedWindCache {
    indices: HashMap<WindCacheKey, usize>,
    values: Vec<Vec<[f32; 4]>>,
}

impl SharedWindCache {
    #[inline(always)]
    fn index(&self, key: WindCacheKey) -> Option<usize> {
        self.indices.get(&key).copied()
    }

    fn lookup_index(&self, key_index: usize, cell: usize) -> Option<[[f32; 4]; 3]> {
        let values = self.values.get(key_index)?;
        let base = cell.checked_mul(3)?;
        Some([
            *values.get(base)?,
            *values.get(base + 1)?,
            *values.get(base + 2)?,
        ])
    }
}

struct SharedTerrainCache {
    values: Vec<f32>,
}

struct SharedMotionCache {
    keys: Vec<LayerCacheKey>,
    indices: HashMap<LayerCacheKey, usize>,
    layers: Vec<Vec<[CloudLayerSample; 3]>>,
    scalars: Vec<Vec<[f32; 6]>>,
}

impl SharedMotionCache {
    #[inline(always)]
    fn index(&self, key: LayerCacheKey) -> Option<usize> {
        self.indices.get(&key).copied()
    }

    #[inline(always)]
    fn layers_for_cell(&self, key_index: usize, cell: usize) -> Option<[CloudLayerSample; 3]> {
        self.layers
            .get(key_index)
            .and_then(|values| values.get(cell))
            .copied()
    }

    #[inline(always)]
    fn layers_for_cell_ref(&self, key_index: usize, cell: usize) -> Option<&[CloudLayerSample; 3]> {
        self.layers
            .get(key_index)
            .and_then(|values| values.get(cell))
    }

    #[inline(always)]
    fn layer_for_cell(
        &self,
        key_index: usize,
        cell: usize,
        layer: usize,
    ) -> Option<CloudLayerSample> {
        self.layers
            .get(key_index)
            .and_then(|values| values.get(cell))
            .and_then(|clouds| clouds.get(layer))
            .copied()
    }

    #[inline(always)]
    fn scalar(&self, key_index: usize, cell: usize, layer: usize, slot: usize) -> Option<f32> {
        self.scalars
            .get(key_index)
            .and_then(|values| values.get(cell))
            .and_then(|values| values.get(layer.checked_mul(2)?.checked_add(slot)?))
            .copied()
    }

    fn layers_for_horizontal(
        &self,
        key_index: usize,
        cell_count: usize,
        horizontal: &[(usize, f32); 4],
    ) -> Option<[CloudLayerSample; 3]> {
        let mut amount = [0.0f32; 3];
        let mut material_weight = [0.0f32; 3];
        let mut opacity = [0.0f32; 3];
        let mut material_quality = [0.0f32; 3];
        let mut base_m = [0.0f32; 3];
        let mut top_m = [0.0f32; 3];
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 || cell >= cell_count {
                continue;
            }
            let clouds = self.layers_for_cell(key_index, cell)?;
            for layer in 0..3 {
                let cloud = clouds[layer];
                amount[layer] += horizontal_weight * cloud.amount;
                if cloud.amount > 0.0 {
                    material_weight[layer] += horizontal_weight * cloud.amount;
                    opacity[layer] += horizontal_weight * cloud.amount * cloud.opacity;
                    material_quality[layer] +=
                        horizontal_weight * cloud.amount * cloud.material_quality;
                    base_m[layer] += horizontal_weight * cloud.amount * cloud.base_m;
                    top_m[layer] += horizontal_weight * cloud.amount * cloud.top_m;
                }
            }
        }
        Some(std::array::from_fn(|layer| {
            if material_weight[layer] <= 1e-6 {
                return CloudLayerSample::default();
            }
            CloudLayerSample {
                amount: clamp01(amount[layer]),
                opacity: clamp01(opacity[layer] / material_weight[layer]),
                material_quality: clamp01(material_quality[layer] / material_weight[layer]),
                base_m: base_m[layer] / material_weight[layer],
                top_m: top_m[layer] / material_weight[layer],
            }
        }))
    }
}

struct SharedSolarPathSamples {
    bearing_bits: u32,
    nodes_per_cell: usize,
    indices: HashMap<LayerCacheKey, usize>,
    cloud_layers: Vec<Vec<[SharedCloudLayerSample; 3]>>,
    aerosol_paths: Option<Vec<Vec<AerosolPathCache>>>,
}

impl SharedSolarPathSamples {
    #[inline(always)]
    fn index(&self, key: LayerCacheKey) -> Option<usize> {
        self.indices.get(&key).copied()
    }

    fn cloud_layers(
        &self,
        key_index: usize,
        cell: usize,
        path_index: usize,
    ) -> Option<[CloudLayerSample; 3]> {
        let offset = cell
            .checked_mul(self.nodes_per_cell)?
            .checked_add(path_index)?;
        self.cloud_layers
            .get(key_index)?
            .get(offset)
            .copied()
            .map(|clouds| clouds.map(SharedCloudLayerSample::to_cloud))
    }

    #[inline(always)]
    fn cloud_layers_ref(
        &self,
        key_index: usize,
        cell: usize,
        path_index: usize,
    ) -> Option<&[SharedCloudLayerSample; 3]> {
        let offset = cell
            .checked_mul(self.nodes_per_cell)?
            .checked_add(path_index)?;
        self.cloud_layers.get(key_index)?.get(offset)
    }

    #[inline(always)]
    fn aerosol_path(
        &self,
        key_index: usize,
        cell: usize,
        path_index: usize,
    ) -> Option<AerosolPathCache> {
        let offset = cell
            .checked_mul(self.nodes_per_cell)?
            .checked_add(path_index)?;
        self.aerosol_paths
            .as_ref()?
            .get(key_index)?
            .get(offset)
            .copied()
    }
}

struct SharedSolarPathSampleSets {
    paths: Vec<SharedSolarPathSamples>,
    bearing_indices: HashMap<u32, usize>,
}

impl SharedSolarPathSampleSets {
    #[inline(always)]
    fn path_index_for_bearing(&self, bearing_bits: u32) -> Option<usize> {
        self.bearing_indices.get(&bearing_bits).copied()
    }

    fn key_index(&self, key: LayerCacheKey) -> Option<usize> {
        self.paths.first()?.index(key)
    }
}

fn interpolated_aerosol_column_from_columns(
    columns: &[AerosolColumn],
    horizontal: &[(usize, f32); 4],
) -> AerosolColumn {
    let mut aod550 = 0.0f32;
    let mut aod_total_weight = 0.0f32;
    let mut total_weight = 0.0f32;
    let mut extinction_sums = [0.0f32; AEROSOL_PROFILE_LEVELS];
    for &(cell, horizontal_weight) in horizontal {
        if horizontal_weight <= 0.0 || cell >= columns.len() {
            continue;
        }
        let column = columns[cell];
        if column.aod550.is_finite() {
            aod550 += horizontal_weight * column.aod550;
            aod_total_weight += horizontal_weight;
        }
        total_weight += horizontal_weight;
        for level in 0..AEROSOL_PROFILE_LEVELS {
            extinction_sums[level] += horizontal_weight * column.extinction_m_inv[level];
        }
    }
    let aod550 = if aod_total_weight > 0.0 {
        aod550 / aod_total_weight
    } else {
        0.0
    };
    let extinction_m_inv = extinction_sums.map(|value| {
        if total_weight > 0.0 {
            value / total_weight
        } else {
            f32::NAN
        }
    });
    AerosolColumn {
        aod550,
        extinction_m_inv,
    }
}

#[derive(Clone, Copy)]
struct DestinationPathOrigin {
    lat_bits: u32,
    lon_bits: u32,
    origin_lon_rad: f32,
    origin_lat_sin: f32,
    origin_lat_cos: f32,
}

impl DestinationPathOrigin {
    fn from_lat_lon(lat: f32, lon: f32) -> Self {
        let lat_rad = lat.to_radians();
        let (origin_lat_sin, origin_lat_cos) = lat_rad.sin_cos();
        Self {
            lat_bits: lat.to_bits(),
            lon_bits: lon.to_bits(),
            origin_lon_rad: lon.to_radians(),
            origin_lat_sin,
            origin_lat_cos,
        }
    }
}

#[derive(Clone, Copy)]
struct DestinationPathGeometry {
    origin_lon_rad: f32,
    origin_lat_sin: f32,
    origin_lat_cos: f32,
    bearing_sin: f32,
    bearing_cos: f32,
}

impl DestinationPathGeometry {
    fn new(origin_lat: f32, origin_lon: f32, bearing_deg: f32) -> Self {
        let origin = DestinationPathOrigin::from_lat_lon(origin_lat, origin_lon);
        let bearing_rad = bearing_deg.to_radians();
        let (bearing_sin, bearing_cos) = bearing_rad.sin_cos();
        Self::from_origin_with_bearing_trig(origin, bearing_sin, bearing_cos)
    }

    fn from_origin_with_bearing_trig(
        origin: DestinationPathOrigin,
        bearing_sin: f32,
        bearing_cos: f32,
    ) -> Self {
        Self {
            origin_lon_rad: origin.origin_lon_rad,
            origin_lat_sin: origin.origin_lat_sin,
            origin_lat_cos: origin.origin_lat_cos,
            bearing_sin,
            bearing_cos,
        }
    }

    fn target_point(self, distance_km: f32) -> (f32, f32) {
        let angular_distance = distance_km / EARTH_RADIUS_KM;
        let (sin, cos) = angular_distance.sin_cos();
        self.target_point_with_trig(sin, cos)
    }

    fn target_point_with_trig(
        self,
        angular_distance_sin: f32,
        angular_distance_cos: f32,
    ) -> (f32, f32) {
        let lat2 = (self.origin_lat_sin * angular_distance_cos
            + self.origin_lat_cos * angular_distance_sin * self.bearing_cos)
            .asin();
        let lon2 = self.origin_lon_rad
            + (self.bearing_sin * angular_distance_sin * self.origin_lat_cos)
                .atan2(angular_distance_cos - self.origin_lat_sin * lat2.sin());
        (lat2.to_degrees(), lon2.to_degrees())
    }
}

#[derive(Clone, Copy)]
struct ObserverCellGeometry {
    cell: usize,
    lat: f32,
    lon: f32,
    north_km: f32,
    east_km: f32,
    bearing_deg: f32,
    distance_km: f32,
    area_weight: f32,
    local_cell_radius_km: f32,
}

#[derive(Clone, Copy)]
struct PendingSolarCell {
    geometry_index: usize,
    observer_cell_sample: bool,
    target_bearing: f32,
    // The geometry table is immutable between the collection and evaluation
    // passes. Cloud samples, risks, active layers and target coordinates live
    // only in the work record; active_mask == 0 replaces the former None.
    work: SolarRayWork,
}

#[derive(Clone, Copy)]
struct ObserverGeometryRow {
    start: usize,
    end: usize,
    first_col: usize,
}

#[derive(Clone, Copy, Default)]
struct ObserverPathSpan {
    start: usize,
    len: usize,
}

#[derive(Clone, Copy)]
struct ObserverMidpointGeometry {
    distance_bits: u32,
    horizontal: [(usize, f32); 4],
    tangent: f32,
}

impl Default for ObserverMidpointGeometry {
    fn default() -> Self {
        Self {
            distance_bits: u32::MAX,
            horizontal: [(0, 0.0); 4],
            tangent: 0.0,
        }
    }
}

#[derive(Clone, Copy)]
struct ObserverGeometryTemplateCell {
    col_offset: isize,
    lat: f32,
    north_km: f32,
    east_km: f32,
    bearing_deg: f32,
    distance_km: f32,
    area_weight: f32,
    local_cell_radius_km: f32,
}

#[derive(Clone, Copy)]
struct ObserverGeometryTemplateRow {
    start: usize,
    end: usize,
    target_row: usize,
}

struct ObserverGeometryTemplate {
    rows: Vec<ObserverGeometryTemplateRow>,
    cells: Vec<ObserverGeometryTemplateCell>,
}

struct ObserverGeometryTemplates {
    by_observer_row: Vec<ObserverGeometryTemplate>,
    path_origins: Vec<DestinationPathOrigin>,
}

impl ObserverGeometryTemplates {
    fn build(
        observers: &[f32],
        observer_count: usize,
        rows: usize,
        cols: usize,
        lat0: f32,
        lon0: f32,
        spacing: f32,
        radius_km: f32,
    ) -> Option<Self> {
        const MIN_OBSERVERS: usize = 128;
        const MAX_ESTIMATED_CELLS: usize = 4_000_000;
        if observer_count < MIN_OBSERVERS
            || rows == 0
            || cols == 0
            || !lat0.is_finite()
            || !lon0.is_finite()
            || !spacing.is_finite()
            || spacing <= 0.0
            || !radius_km.is_finite()
            || radius_km <= 0.0
            || observers.len() != observer_count.saturating_mul(OBSERVER_FIELDS)
        {
            return None;
        }

        for observer_index in 0..observer_count {
            let offset = observer_index * OBSERVER_FIELDS;
            let latitude = observers[offset];
            let longitude = observers[offset + 1];
            if !latitude.is_finite() || !longitude.is_finite() {
                return None;
            }
            let observer_row = ((latitude - lat0) / spacing).round() as isize;
            let observer_col = ((longitude - lon0) / spacing).round() as isize;
            if observer_row < 0
                || observer_col < 0
                || observer_row >= rows as isize
                || observer_col >= cols as isize
            {
                return None;
            }
            let expected_lat = lat0 + observer_row as f32 * spacing;
            let expected_lon = lon0 + observer_col as f32 * spacing;
            if (latitude - expected_lat).abs() > 1e-4 || (longitude - expected_lon).abs() > 1e-4 {
                return None;
            }
        }

        let radius_sq = radius_km * radius_km;
        let lat_radius = radius_km / 110.574;
        let max_row_delta = (lat_radius / spacing).ceil() as usize + 1;
        let estimated_row_span = max_row_delta.saturating_mul(2).saturating_add(1);
        let estimated_col_span = (0..rows)
            .map(|row| {
                let latitude = lat0 + row as f32 * spacing;
                let longitude_radius =
                    radius_km / (111.320 * latitude.to_radians().cos().abs().max(0.2));
                (longitude_radius / spacing).ceil() as usize * 2 + 3
            })
            .max()
            .unwrap_or(0);
        let estimated_cells = rows
            .saturating_mul(estimated_row_span)
            .saturating_mul(estimated_col_span);
        if estimated_cells > MAX_ESTIMATED_CELLS {
            return None;
        }

        let path_origins = (0..rows.saturating_mul(cols))
            .map(|cell| {
                let row = cell / cols;
                let col = cell % cols;
                DestinationPathOrigin::from_lat_lon(
                    lat0 + row as f32 * spacing,
                    lon0 + col as f32 * spacing,
                )
            })
            .collect();

        let mut by_observer_row = Vec::with_capacity(rows);
        for observer_row in 0..rows {
            let observer_lat = lat0 + observer_row as f32 * spacing;
            let longitude_radius =
                radius_km / (111.320 * observer_lat.to_radians().cos().abs().max(0.2));
            let max_col_delta = (longitude_radius / spacing).ceil() as usize + 1;
            let row_min = observer_row.saturating_sub(max_row_delta);
            let row_max = observer_row
                .saturating_add(max_row_delta)
                .min(rows.saturating_sub(1));
            let mut template_rows = Vec::new();
            let mut template_cells = Vec::new();
            for target_row in row_min..=row_max {
                let target_lat = lat0 + target_row as f32 * spacing;
                let north_km = (target_lat - observer_lat) * 110.574;
                let row_bearing_cos = ((observer_lat + target_lat) * 0.5).to_radians().cos();
                let row_start = template_cells.len();
                for col_offset in -(max_col_delta as isize)..=(max_col_delta as isize) {
                    let east_km = col_offset as f32 * spacing * 111.320 * row_bearing_cos;
                    let Some((_distance_sq, distance_km)) =
                        radius_distance_km(north_km, east_km, radius_km, radius_sq)
                    else {
                        continue;
                    };
                    template_cells.push(ObserverGeometryTemplateCell {
                        col_offset,
                        lat: target_lat,
                        north_km,
                        east_km,
                        bearing_deg: east_km.atan2(north_km).to_degrees().rem_euclid(360.0),
                        distance_km,
                        area_weight: target_lat.to_radians().cos().abs().max(0.2),
                        local_cell_radius_km: local_cell_radius_km(spacing, target_lat),
                    });
                }
                if row_start < template_cells.len() {
                    template_rows.push(ObserverGeometryTemplateRow {
                        start: row_start,
                        end: template_cells.len(),
                        target_row,
                    });
                }
            }
            by_observer_row.push(ObserverGeometryTemplate {
                rows: template_rows,
                cells: template_cells,
            });
        }
        Some(Self {
            by_observer_row,
            path_origins,
        })
    }
}

fn fire_cloud_illumination_from_aerosol_tau(optical_depth: f32) -> f32 {
    if !optical_depth.is_finite() {
        return 0.0;
    }
    // The input transmission is physical Beer-Lambert attenuation. The output
    // is deliberately a calibrated visibility/exposure response, not a
    // radiance or probability: the breakpoints encode the finite human/camera
    // dynamic range and must not be described as a radiative-transfer law.
    // The raw transmission remains the physical diagnostic used separately.
    const POINTS: [(f32, f32); 6] = [
        (1.0 / 5000.0, 0.0),
        (1.0 / 1000.0, 0.15),
        (1.0 / 500.0, 0.35),
        (1.0 / 50.0, 0.85),
        (1.0 / 20.0, 1.0),
        (1.0, 1.0),
    ];
    let transmission = (-optical_depth.clamp(0.0, 20.0)).exp();
    if transmission <= POINTS[0].0 {
        return 0.0;
    }
    for pair in POINTS.windows(2) {
        let (left_transmission, left_illumination) = pair[0];
        let (right_transmission, right_illumination) = pair[1];
        if transmission <= right_transmission {
            let weight = (transmission.ln() - left_transmission.ln())
                / (right_transmission.ln() - left_transmission.ln());
            return left_illumination
                + weight.clamp(0.0, 1.0) * (right_illumination - left_illumination);
        }
    }
    1.0
}

fn observer_cloud_contrast_from_aerosol_tau(optical_depth: f32) -> f32 {
    if !optical_depth.is_finite() {
        return 0.0;
    }
    // This return leg is not another direct-sun illumination test. Extinction
    // lowers the contrast and saturation of an already illuminated cloud while
    // visual adaptation and photographic exposure recover part of the absolute
    // radiance loss. Keep that response gradual until the direct contrast drops
    // below the tutorial's 2% visibility reference, then fade it out toward the
    // per-mille range where low-angle detail is effectively hidden by haze.
    let transmission = (-optical_depth.clamp(0.0, 20.0)).exp();
    // Bot's published examples and cross-section diagnostics reduce quality
    // by roughly 6-29% for ordinary AOD scenes rather than multiplying the
    // result by raw Beer-Lambert transmission. A 0.025 perceptual exponent
    // keeps the return-path correction gradual while the gate below still
    // removes detail when the return beam falls into the per-mille range.
    let adapted_contrast = transmission.powf(OBSERVER_CONTRAST_PERCEPTUAL_EXPONENT);
    clamp01(adapted_contrast * smooth_gate(transmission, 1.0 / 5000.0, 1.0 / 1000.0))
}

fn observer_return_transmission_from_aerosol_tau(optical_depth: f32) -> f32 {
    if !optical_depth.is_finite() || optical_depth < 0.0 {
        return 0.0;
    }
    // This is the physical aerosol-only return-beam quantity. It is not a
    // visibility or camera score: cloud radiance, sky radiance and path
    // scattering are intentionally not folded into this diagnostic.
    (-optical_depth.min(100.0)).exp()
}

fn normalized_observer_return_diagnostics(optical_depth: f32) -> (f32, f32) {
    let optical_depth = if optical_depth.is_finite() && optical_depth >= 0.0 {
        optical_depth.min(100.0)
    } else {
        100.0
    };
    (
        optical_depth,
        clamp01(observer_return_transmission_from_aerosol_tau(optical_depth)),
    )
}

fn signed_angular_difference(value: f32, reference: f32) -> f32 {
    (value - reference + 180.0).rem_euclid(360.0) - 180.0
}

fn radius_squared_prefilter_allows(distance_sq: f32, radius_sq: f32) -> bool {
    distance_sq <= radius_sq * (1.0 + RADIUS_SQUARED_PREFILTER_MARGIN)
}

fn radius_distance_km(
    north_km: f32,
    east_km: f32,
    radius_km: f32,
    radius_sq: f32,
) -> Option<(f32, f32)> {
    let distance_sq = north_km * north_km + east_km * east_km;
    if !radius_squared_prefilter_allows(distance_sq, radius_sq) {
        return None;
    }
    let distance = north_km.hypot(east_km);
    if distance > radius_km {
        return None;
    }
    Some((distance_sq, distance))
}

fn local_cell_radius_km(cell_spacing_deg: f32, latitude_deg: f32) -> f32 {
    0.5 * cell_spacing_deg.abs()
        * (110.574 * 111.320 * latitude_deg.to_radians().cos().abs().max(0.2)).sqrt()
}

fn cell_half_extents_km(cell_spacing_deg: f32, latitude_deg: f32) -> (f32, f32) {
    let half_spacing = 0.5 * cell_spacing_deg.abs();
    let north_km = half_spacing * 110.574;
    let east_km = half_spacing * 111.320 * latitude_deg.to_radians().cos().abs().max(0.2);
    (north_km, east_km)
}

fn containing_cell_exit_distance_km(
    cell_bearing_deg: f32,
    cell_distance_km: f32,
    ray_bearing_deg: f32,
    cell_spacing_deg: f32,
    latitude_deg: f32,
) -> Option<f32> {
    let (half_north_km, half_east_km) = cell_half_extents_km(cell_spacing_deg, latitude_deg);
    let cell_bearing = cell_bearing_deg.to_radians();
    let center_north_km = cell_distance_km * cell_bearing.cos();
    let center_east_km = cell_distance_km * cell_bearing.sin();
    let south_km = center_north_km - half_north_km;
    let north_km = center_north_km + half_north_km;
    let west_km = center_east_km - half_east_km;
    let east_km = center_east_km + half_east_km;
    if south_km > 0.0 || north_km < 0.0 || west_km > 0.0 || east_km < 0.0 {
        return None;
    }

    let ray_bearing = ray_bearing_deg.to_radians();
    let ray_north = ray_bearing.cos();
    let ray_east = ray_bearing.sin();
    let north_exit = if ray_north > 1e-6 {
        north_km / ray_north
    } else if ray_north < -1e-6 {
        south_km / ray_north
    } else {
        f32::INFINITY
    };
    let east_exit = if ray_east > 1e-6 {
        east_km / ray_east
    } else if ray_east < -1e-6 {
        west_km / ray_east
    } else {
        f32::INFINITY
    };
    Some(north_exit.min(east_exit).max(0.0))
}

#[derive(Clone, Copy)]
struct AngularCloudFootprint {
    azimuth_start_deg: f32,
    azimuth_end_deg: f32,
    remote_base_deg: f32,
    remote_top_deg: f32,
    source_top_m: f32,
    azimuth_bin_start: usize,
    azimuth_bin_end: usize,
    contains_observer: bool,
}

#[allow(clippy::too_many_arguments)]
fn angular_cloud_footprint(
    bearing: f32,
    solar_azimuth: f32,
    distance_km: f32,
    cloud_base_m: f32,
    cloud_top_m: f32,
    cell_spacing_deg: f32,
    latitude_deg: f32,
) -> AngularCloudFootprint {
    angular_cloud_footprint_with_radius(
        bearing,
        solar_azimuth,
        distance_km,
        cloud_base_m,
        cloud_top_m,
        cell_spacing_deg,
        latitude_deg,
        local_cell_radius_km(cell_spacing_deg, latitude_deg),
    )
}

#[allow(clippy::too_many_arguments)]
fn angular_cloud_footprint_with_radius(
    bearing: f32,
    solar_azimuth: f32,
    distance_km: f32,
    cloud_base_m: f32,
    cloud_top_m: f32,
    cell_spacing_deg: f32,
    latitude_deg: f32,
    footprint_radius_km: f32,
) -> AngularCloudFootprint {
    let center_deg = signed_angular_difference(bearing, solar_azimuth);
    let contains_observer = if cell_spacing_deg.is_finite()
        && distance_km.is_finite()
        && distance_km > cell_spacing_deg.abs() * CONTAINING_CELL_MAX_DISTANCE_PER_DEG_KM
    {
        false
    } else {
        containing_cell_exit_distance_km(
            bearing,
            distance_km,
            solar_azimuth,
            cell_spacing_deg,
            latitude_deg,
        )
        .is_some()
    };
    let (azimuth_start_deg, azimuth_end_deg) = if contains_observer {
        (-VIEW_AZIMUTH_HALF_DEG, VIEW_AZIMUTH_HALF_DEG)
    } else {
        let half_width = (footprint_radius_km / distance_km.max(1.0))
            .atan()
            .to_degrees();
        (
            (center_deg - half_width).max(-VIEW_AZIMUTH_HALF_DEG),
            (center_deg + half_width).min(VIEW_AZIMUTH_HALF_DEG),
        )
    };
    let far_distance_km = (distance_km + footprint_radius_km).max(1.0);
    let near_distance_km = (distance_km - footprint_radius_km).max(1.0);
    let remote_base_deg = apparent_elevation(far_distance_km, cloud_base_m)
        .clamp(VIEW_SUB_HORIZON_FLOOR_DEG, VIEW_ELEVATION_BINS as f32);
    let remote_top_deg = if contains_observer {
        VIEW_ELEVATION_BINS as f32
    } else {
        apparent_elevation(near_distance_km, cloud_top_m)
            .clamp(VIEW_SUB_HORIZON_FLOOR_DEG, VIEW_ELEVATION_BINS as f32)
    };
    let azimuth_bin_start = (((azimuth_start_deg + VIEW_AZIMUTH_HALF_DEG) / VIEW_AZIMUTH_BIN_DEG)
        .floor() as isize)
        .clamp(0, VIEW_AZIMUTH_BINS as isize) as usize;
    let azimuth_bin_end = (((azimuth_end_deg + VIEW_AZIMUTH_HALF_DEG) / VIEW_AZIMUTH_BIN_DEG).ceil()
        as isize)
        .clamp(0, VIEW_AZIMUTH_BINS as isize) as usize;
    AngularCloudFootprint {
        azimuth_start_deg,
        azimuth_end_deg,
        remote_base_deg,
        remote_top_deg,
        source_top_m: cloud_top_m,
        azimuth_bin_start,
        azimuth_bin_end,
        contains_observer,
    }
}

#[allow(clippy::too_many_arguments)]
fn for_each_angular_cloud_cell_precomputed(
    footprint: AngularCloudFootprint,
    bearing: f32,
    solar_azimuth: f32,
    distance_km: f32,
    cloud_base_m: f32,
    cell_spacing_deg: f32,
    latitude_deg: f32,
    mut visit: impl FnMut(usize, f32),
) {
    if footprint.azimuth_end_deg <= footprint.azimuth_start_deg {
        return;
    }
    for azimuth_bin in footprint.azimuth_bin_start..footprint.azimuth_bin_end {
        let bin_start = -VIEW_AZIMUTH_HALF_DEG + azimuth_bin as f32 * VIEW_AZIMUTH_BIN_DEG;
        let bin_end = bin_start + VIEW_AZIMUTH_BIN_DEG;
        let bin_center = 0.5 * (bin_start + bin_end);
        let azimuth_overlap = ((footprint.azimuth_end_deg.min(bin_end)
            - footprint.azimuth_start_deg.max(bin_start))
            / VIEW_AZIMUTH_BIN_DEG)
            .clamp(0.0, 1.0);
        let base = if footprint.contains_observer {
            let exit_distance_km = containing_cell_exit_distance_km(
                bearing,
                distance_km,
                solar_azimuth + bin_center,
                cell_spacing_deg,
                latitude_deg,
            )
            .unwrap_or(0.0);
            apparent_elevation(exit_distance_km.max(0.001), cloud_base_m)
                .clamp(VIEW_SUB_HORIZON_FLOOR_DEG, VIEW_ELEVATION_BINS as f32)
        } else {
            footprint.remote_base_deg
        };
        let top = footprint.remote_top_deg;
        if top <= base || top <= VIEW_SUB_HORIZON_FLOOR_DEG || base >= VIEW_ELEVATION_BINS as f32 {
            continue;
        }
        let elevation_start = base.floor().clamp(0.0, VIEW_ELEVATION_BINS as f32) as usize;
        let elevation_end = top.ceil().clamp(0.0, VIEW_ELEVATION_BINS as f32) as usize;
        for elevation_bin in elevation_start..elevation_end {
            let bin_base = elevation_bin as f32;
            // Bin 0 owns the sub-horizon skirt as well: a deck whose top is
            // between -1.5 and 0 degrees still occludes the refracted sun.
            let bin_low = if elevation_bin == 0 {
                VIEW_SUB_HORIZON_FLOOR_DEG
            } else {
                bin_base
            };
            let elevation_overlap = (top.min(bin_base + 1.0) - base.max(bin_low)).clamp(0.0, 1.0);
            let overlap = azimuth_overlap * elevation_overlap;
            if overlap <= 0.0 {
                continue;
            }
            let index = azimuth_bin * VIEW_ELEVATION_BINS + elevation_bin;
            visit(index, overlap);
        }
    }
}

#[cfg(test)]
fn add_angular_cloud_footprint(
    opacity: &mut [u8; VIEW_ANGULAR_CELLS],
    bearing: f32,
    solar_azimuth: f32,
    distance_km: f32,
    cloud_base_m: f32,
    cloud_top_m: f32,
    cloud_fraction: f32,
    cell_spacing_deg: f32,
    latitude_deg: f32,
) {
    for_each_angular_cloud_cell(
        bearing,
        solar_azimuth,
        distance_km,
        cloud_base_m,
        cloud_top_m,
        cell_spacing_deg,
        latitude_deg,
        |index, overlap| {
            let added =
                (clamp01(cloud_fraction) * overlap * VIEW_OPACITY_MAX as f32).round() as u16;
            if added > 0 {
                opacity[index] = opacity[index].max(added as u8);
            }
        },
    );
}

#[allow(clippy::too_many_arguments)]
fn for_each_angular_cloud_cell(
    bearing: f32,
    solar_azimuth: f32,
    distance_km: f32,
    cloud_base_m: f32,
    cloud_top_m: f32,
    cell_spacing_deg: f32,
    latitude_deg: f32,
    visit: impl FnMut(usize, f32),
) {
    let footprint = angular_cloud_footprint(
        bearing,
        solar_azimuth,
        distance_km,
        cloud_base_m,
        cloud_top_m,
        cell_spacing_deg,
        latitude_deg,
    );
    for_each_angular_cloud_cell_precomputed(
        footprint,
        bearing,
        solar_azimuth,
        distance_km,
        cloud_base_m,
        cell_spacing_deg,
        latitude_deg,
        visit,
    );
}

#[derive(Clone, Copy)]
struct CloudProjection {
    audit_slot: usize,
    angular_footprint: AngularCloudFootprint,
    target_cell_index: usize,
    distance_km: f32,
    bearing_deg: f32,
    latitude_deg: f32,
    base_m: f32,
    amount: f32,
    target_opacity: f32,
    material_quality: f32,
    burnable_fraction: f32,
    incoming_aerosol_illumination: f32,
    observer_aerosol_contrast: f32,
    observer_aerosol_optical_depth: f32,
    observer_aerosol_transmission: f32,
    target_deck_connected: f32,
    target_deck_attenuated_fraction: f32,
    target_deep_risk: f32,
    cloud_optical_depth: f32,
    peak_blocking: f32,
    clear_gap_count: f32,
    target_base_asl_m: f32,
    target_top_asl_m: f32,
    target_terrain_asl_m: f32,
    layer: usize,
    target_eligible: bool,
    geometry_reachable: bool,
    external_path_transmission: f32,
    observer_path_blocked: bool,
}

#[inline]
fn compare_cloud_projection_order(left: &CloudProjection, right: &CloudProjection) -> Ordering {
    // Projection distances are produced by `hypot` after the radius gate, so
    // they are non-negative finite values. For that domain, IEEE-754 bit
    // order is identical to `f32::total_cmp` and avoids its sign normalization
    // on every sort comparison.
    left.distance_km
        .to_bits()
        .cmp(&right.distance_km.to_bits())
        .then_with(|| right.layer.cmp(&left.layer))
}

#[allow(clippy::too_many_arguments)]
fn for_each_projection_angular_cloud_cell(
    cloud: CloudProjection,
    solar_azimuth: f32,
    cell_spacing_deg: f32,
    use_precomputed: bool,
    visit: impl FnMut(usize, f32),
) {
    if use_precomputed {
        for_each_angular_cloud_cell_precomputed(
            cloud.angular_footprint,
            cloud.bearing_deg,
            solar_azimuth,
            cloud.distance_km,
            cloud.base_m,
            cell_spacing_deg,
            cloud.latitude_deg,
            visit,
        );
    } else {
        for_each_angular_cloud_cell(
            cloud.bearing_deg,
            solar_azimuth,
            cloud.distance_km,
            cloud.base_m,
            cloud.angular_footprint.source_top_m,
            cell_spacing_deg,
            cloud.latitude_deg,
            visit,
        );
    }
}

#[derive(Clone, Copy)]
struct TargetAuditAccumulator {
    projection: CloudProjection,
    candidate_overlap_solid_angle: f32,
    visible_overlap_solid_angle: f32,
    burn_contribution_solid_angle: f32,
    external_tau: f32,
    target_self_tau: f32,
    path_audit_available: bool,
}

impl TargetAuditAccumulator {
    fn new(projection: CloudProjection) -> Self {
        Self {
            projection,
            candidate_overlap_solid_angle: 0.0,
            visible_overlap_solid_angle: 0.0,
            burn_contribution_solid_angle: 0.0,
            external_tau: 0.0,
            target_self_tau: 0.0,
            path_audit_available: false,
        }
    }
}

#[derive(Clone, Copy, Default)]
struct PathAuditRecord {
    external_tau: f32,
    target_self_tau: f32,
    pre_peak_external_transmission: f32,
    peak_factor: f32,
    trace_steps: u32,
    unique_cells: u32,
    revisited_cells: u32,
    domain_break: bool,
    ray_height_break: bool,
    opaque_cutoff: bool,
    target_extension_count_max: u32,
    target_cell_index: Option<usize>,
    target_opacity: f32,
}

#[derive(Clone, Copy, Default)]
pub struct PathAuditSummary {
    visible_weight: f32,
    external_tau_weighted_sum: f32,
    target_self_tau_weighted_sum: f32,
    pre_peak_external_transmission_weighted_sum: f32,
    peak_factor_weighted_sum: f32,
    trace_steps_max: u32,
    unique_cells_max: u32,
    revisited_cells_max: u32,
    domain_break_count: u32,
    ray_height_break_count: u32,
    opaque_cutoff_count: u32,
    family_tau_weighted_sum: [f32; 3],
    target_extension_count_max: u32,
    pb_loss_total: f32,
    pb_decomposition_error: f32,
    total_self_loss_solid_angle: f32,
    dominant_self_loss_solid_angle: f32,
    dominant_projection: Option<CloudProjection>,
    dominant_audit_record: Option<PathAuditRecord>,
}

impl PathAuditSummary {
    fn write_to(self, output: &mut [f32]) {
        debug_assert_eq!(output.len(), PATH_AUDIT_FIELDS);
        output.fill(0.0);
        output[PATH_AUDIT_SCHEMA_VERSION_OFFSET] = PATH_AUDIT_SCHEMA_VERSION;
        output[PATH_AUDIT_EXTERNAL_TAU_OFFSET] =
            conditional_weighted_mean(self.external_tau_weighted_sum, self.visible_weight);
        output[PATH_AUDIT_TARGET_SELF_TAU_OFFSET] =
            conditional_weighted_mean(self.target_self_tau_weighted_sum, self.visible_weight);
        output[PATH_AUDIT_PRE_PEAK_EXTERNAL_FIELD_OFFSET] =
            angular_solid_angle_field(self.pre_peak_external_transmission_weighted_sum);
        output[PATH_AUDIT_PEAK_FACTOR_OFFSET] =
            conditional_weighted_mean(self.peak_factor_weighted_sum, self.visible_weight);
        output[PATH_AUDIT_TRACE_STEPS_MAX_OFFSET] = self.trace_steps_max as f32;
        output[PATH_AUDIT_UNIQUE_CELLS_MAX_OFFSET] = self.unique_cells_max as f32;
        output[PATH_AUDIT_REVISITED_CELLS_MAX_OFFSET] = self.revisited_cells_max as f32;
        output[PATH_AUDIT_DOMAIN_BREAK_COUNT_OFFSET] = self.domain_break_count as f32;
        output[PATH_AUDIT_RAY_HEIGHT_BREAK_COUNT_OFFSET] = self.ray_height_break_count as f32;
        output[PATH_AUDIT_OPAQUE_CUTOFF_COUNT_OFFSET] = self.opaque_cutoff_count as f32;
        output[PATH_AUDIT_FAMILY_TAU_HIGH_OFFSET] =
            conditional_weighted_mean(self.family_tau_weighted_sum[0], self.visible_weight);
        output[PATH_AUDIT_FAMILY_TAU_MID_OFFSET] =
            conditional_weighted_mean(self.family_tau_weighted_sum[1], self.visible_weight);
        output[PATH_AUDIT_FAMILY_TAU_LOW_OFFSET] =
            conditional_weighted_mean(self.family_tau_weighted_sum[2], self.visible_weight);
        output[PATH_AUDIT_TARGET_EXTENSION_COUNT_MAX_OFFSET] =
            self.target_extension_count_max as f32;
        output[PATH_AUDIT_VISIBLE_WEIGHT_OFFSET] = self.visible_weight;
        output[PATH_AUDIT_PB_LOSS_TOTAL_OFFSET] = self.pb_loss_total;
        output[PATH_AUDIT_PB_DECOMPOSITION_ERROR_OFFSET] = self.pb_decomposition_error;
        let dominant_loss_field = angular_solid_angle_field(self.dominant_self_loss_solid_angle);
        let total_loss_field = angular_solid_angle_field(self.total_self_loss_solid_angle);
        output[PATH_AUDIT_DOMINANT_LOSS_FIELD_OFFSET] = dominant_loss_field;
        output[PATH_AUDIT_DOMINANT_SHARE_OFFSET] = if self.total_self_loss_solid_angle > 1e-6 {
            (self.dominant_self_loss_solid_angle / self.total_self_loss_solid_angle).clamp(0.0, 1.0)
        } else {
            0.0
        };
        let Some(projection) = self.dominant_projection else {
            output[PATH_AUDIT_DOMINANT_VALID_OFFSET] = 0.0;
            output[PATH_AUDIT_DOMINANT_TARGET_CELL_INDEX_OFFSET] = -1.0;
            output[PATH_AUDIT_DOMINANT_FAMILY_CODE_OFFSET] = -1.0;
            output[PATH_AUDIT_OMITTED_LOSS_FIELD_OFFSET] = total_loss_field;
            return;
        };
        let Some(record) = self.dominant_audit_record else {
            output[PATH_AUDIT_DOMINANT_VALID_OFFSET] = 0.0;
            output[PATH_AUDIT_DOMINANT_TARGET_CELL_INDEX_OFFSET] = -1.0;
            output[PATH_AUDIT_DOMINANT_FAMILY_CODE_OFFSET] = -1.0;
            output[PATH_AUDIT_OMITTED_LOSS_FIELD_OFFSET] = total_loss_field;
            return;
        };
        output[PATH_AUDIT_DOMINANT_VALID_OFFSET] = 1.0;
        output[PATH_AUDIT_DOMINANT_TARGET_CELL_INDEX_OFFSET] =
            record.target_cell_index.map_or(-1.0, |index| index as f32);
        output[PATH_AUDIT_DOMINANT_FAMILY_CODE_OFFSET] = (projection.layer + 1) as f32;
        output[PATH_AUDIT_DOMINANT_DISTANCE_KM_OFFSET] = projection.distance_km;
        output[PATH_AUDIT_DOMINANT_BEARING_DEG_OFFSET] = projection.bearing_deg;
        output[PATH_AUDIT_DOMINANT_AMOUNT_OFFSET] = projection.amount;
        output[PATH_AUDIT_DOMINANT_BASE_ASL_OFFSET] = projection.target_base_asl_m;
        output[PATH_AUDIT_DOMINANT_TOP_ASL_OFFSET] = projection.target_top_asl_m;
        output[PATH_AUDIT_DOMINANT_TERRAIN_ASL_OFFSET] = projection.target_terrain_asl_m;
        output[PATH_AUDIT_DOMINANT_MATERIAL_QUALITY_OFFSET] = projection.material_quality;
        output[PATH_AUDIT_DOMINANT_TARGET_OPACITY_OFFSET] = record.target_opacity;
        output[PATH_AUDIT_DOMINANT_TARGET_DEEP_RISK_OFFSET] = projection.target_deep_risk;
        output[PATH_AUDIT_DOMINANT_EFFECTIVE_SELF_TRANSMISSION_OFFSET] =
            if projection.external_path_transmission > 1e-6 {
                (projection.burnable_fraction / projection.external_path_transmission)
                    .clamp(0.0, 1.0)
            } else {
                0.0
            };
        output[PATH_AUDIT_DOMINANT_TARGET_DECK_CONNECTED_OFFSET] = projection.target_deck_connected;
        output[PATH_AUDIT_DOMINANT_TARGET_DECK_ATTENUATED_OFFSET] =
            projection.target_deck_attenuated_fraction;
        output[PATH_AUDIT_OMITTED_LOSS_FIELD_OFFSET] =
            (total_loss_field - dominant_loss_field).max(0.0);
    }
}

fn write_target_audit_records(
    mut records: Vec<TargetAuditAccumulator>,
    output: &mut [f32],
    observer_elevation_m: f32,
) {
    debug_assert_eq!(output.len(), TARGET_AUDIT_MAX_RECORDS * TARGET_AUDIT_FIELDS);
    output.fill(0.0);
    records.retain(|record| {
        record.candidate_overlap_solid_angle > 1e-8
            || record.visible_overlap_solid_angle > 1e-8
            || record.burn_contribution_solid_angle > 1e-8
    });
    records.sort_unstable_by(|left, right| {
        right
            .burn_contribution_solid_angle
            .total_cmp(&left.burn_contribution_solid_angle)
            .then_with(|| {
                right
                    .candidate_overlap_solid_angle
                    .total_cmp(&left.candidate_overlap_solid_angle)
            })
            .then_with(|| {
                left.projection
                    .target_cell_index
                    .cmp(&right.projection.target_cell_index)
            })
            .then_with(|| left.projection.layer.cmp(&right.projection.layer))
    });
    let visible_solid_angle =
        VIEW_AZIMUTH_BINS as f32 * (VIEW_ELEVATION_BINS as f32).to_radians().sin();
    for (index, record) in records
        .into_iter()
        .take(TARGET_AUDIT_MAX_RECORDS)
        .enumerate()
    {
        let offset = index * TARGET_AUDIT_FIELDS;
        let projection = record.projection;
        let target_self_transmission = if projection.external_path_transmission > 1e-6 {
            (projection.burnable_fraction / projection.external_path_transmission).clamp(0.0, 1.0)
        } else {
            0.0
        };
        let burn_field =
            clamp01(record.burn_contribution_solid_angle / visible_solid_angle.max(1e-6));
        debug_assert!(
            burn_field <= 1e-6
                || (projection.target_eligible
                    && projection.geometry_reachable
                    && projection.external_path_transmission > 1e-6
                    && target_self_transmission > 1e-6),
            "target audit burn contribution is not supported by its target/path state"
        );
        output[offset + TARGET_AUDIT_SCHEMA_OFFSET] = TARGET_AUDIT_SCHEMA_VERSION;
        output[offset + TARGET_AUDIT_VALID_OFFSET] = 1.0;
        output[offset + TARGET_AUDIT_CELL_INDEX_OFFSET] = projection.target_cell_index as f32;
        output[offset + TARGET_AUDIT_FAMILY_CODE_OFFSET] = (projection.layer + 1) as f32;
        output[offset + TARGET_AUDIT_DISTANCE_KM_OFFSET] = projection.distance_km;
        output[offset + TARGET_AUDIT_BEARING_DEG_OFFSET] = projection.bearing_deg;
        output[offset + TARGET_AUDIT_AMOUNT_OFFSET] = projection.amount;
        output[offset + TARGET_AUDIT_CANDIDATE_OVERLAP_FIELD_OFFSET] =
            clamp01(record.candidate_overlap_solid_angle / visible_solid_angle.max(1e-6));
        output[offset + TARGET_AUDIT_VISIBLE_OVERLAP_FIELD_OFFSET] =
            clamp01(record.visible_overlap_solid_angle / visible_solid_angle.max(1e-6));
        output[offset + TARGET_AUDIT_BURN_CONTRIBUTION_FIELD_OFFSET] = burn_field;
        output[offset + TARGET_AUDIT_TARGET_ELIGIBLE_OFFSET] =
            if projection.target_eligible { 1.0 } else { 0.0 };
        output[offset + TARGET_AUDIT_GEOMETRY_REACHABLE_OFFSET] = if projection.geometry_reachable {
            1.0
        } else {
            0.0
        };
        output[offset + TARGET_AUDIT_EXTERNAL_PATH_TRANSMISSION_OFFSET] =
            projection.external_path_transmission;
        output[offset + TARGET_AUDIT_TARGET_SELF_TRANSMISSION_OFFSET] = target_self_transmission;
        output[offset + TARGET_AUDIT_EXTERNAL_TAU_OFFSET] = record.external_tau;
        output[offset + TARGET_AUDIT_TARGET_SELF_TAU_OFFSET] = record.target_self_tau;
        output[offset + TARGET_AUDIT_BASE_ASL_OFFSET] = projection.target_base_asl_m;
        output[offset + TARGET_AUDIT_TOP_ASL_OFFSET] = projection.target_top_asl_m;
        output[offset + TARGET_AUDIT_TERRAIN_ASL_OFFSET] = projection.target_terrain_asl_m;
        output[offset + TARGET_AUDIT_BASE_ABOVE_OBSERVER_OFFSET] =
            projection.target_base_asl_m - observer_elevation_m;
        output[offset + TARGET_AUDIT_TOP_ABOVE_OBSERVER_OFFSET] =
            projection.target_top_asl_m - observer_elevation_m;
        output[offset + TARGET_AUDIT_MATERIAL_QUALITY_OFFSET] = projection.material_quality;
        output[offset + TARGET_AUDIT_TARGET_OPACITY_OFFSET] = projection.target_opacity;
        output[offset + TARGET_AUDIT_TARGET_DEEP_RISK_OFFSET] = projection.target_deep_risk;
        output[offset + TARGET_AUDIT_DECK_CONNECTED_OFFSET] = projection.target_deck_connected;
        output[offset + TARGET_AUDIT_DECK_ATTENUATED_OFFSET] =
            projection.target_deck_attenuated_fraction;
        output[offset + TARGET_AUDIT_CLEAR_GAP_COUNT_OFFSET] = projection.clear_gap_count;
        output[offset + TARGET_AUDIT_PATH_AUDIT_AVAILABLE_OFFSET] = if record.path_audit_available {
            1.0
        } else {
            0.0
        };
    }
}

struct SolarRayDiagnostics {
    samples: AtomicU64,
    samples_with_rays: AtomicU64,
    rays: AtomicU64,
    groups4: AtomicU64,
    remainder4: AtomicU64,
    groups8: AtomicU64,
    remainder8: AtomicU64,
    rays_by_layer_count: [AtomicU64; 3],
}

impl SolarRayDiagnostics {
    fn new() -> Self {
        Self {
            samples: AtomicU64::new(0),
            samples_with_rays: AtomicU64::new(0),
            rays: AtomicU64::new(0),
            groups4: AtomicU64::new(0),
            remainder4: AtomicU64::new(0),
            groups8: AtomicU64::new(0),
            remainder8: AtomicU64::new(0),
            rays_by_layer_count: std::array::from_fn(|_| AtomicU64::new(0)),
        }
    }

    fn record(&self, rays: usize, rays_by_layer_count: [usize; 3]) {
        self.samples.fetch_add(1, AtomicOrdering::Relaxed);
        self.samples_with_rays
            .fetch_add(u64::from(rays > 0), AtomicOrdering::Relaxed);
        self.rays.fetch_add(rays as u64, AtomicOrdering::Relaxed);
        self.groups4
            .fetch_add((rays / 4) as u64, AtomicOrdering::Relaxed);
        self.remainder4
            .fetch_add((rays % 4) as u64, AtomicOrdering::Relaxed);
        self.groups8
            .fetch_add((rays / 8) as u64, AtomicOrdering::Relaxed);
        self.remainder8
            .fetch_add((rays % 8) as u64, AtomicOrdering::Relaxed);
        for (counter, count) in self.rays_by_layer_count.iter().zip(rays_by_layer_count) {
            counter.fetch_add(count as u64, AtomicOrdering::Relaxed);
        }
    }

    fn report(&self) -> String {
        let load = |counter: &AtomicU64| counter.load(AtomicOrdering::Relaxed);
        format!(
            "samples={} samples_with_rays={} rays={} groups4={} remainder4={} groups8={} remainder8={} rays_1_layer={} rays_2_layers={} rays_3_layers={}",
            load(&self.samples),
            load(&self.samples_with_rays),
            load(&self.rays),
            load(&self.groups4),
            load(&self.remainder4),
            load(&self.groups8),
            load(&self.remainder8),
            load(&self.rays_by_layer_count[0]),
            load(&self.rays_by_layer_count[1]),
            load(&self.rays_by_layer_count[2]),
        )
    }
}

struct SkyBuffers {
    audit_enabled: bool,
    aerosol_empty: bool,
    aerosol_interpolation_fast_path_safe: bool,
    shared_winds: Option<Arc<SharedWindCache>>,
    shared_terrain: Option<Arc<SharedTerrainCache>>,
    shared_motion: Option<Arc<SharedMotionCache>>,
    shared_solar_path_nodes: Option<Arc<SharedSolarPathNodes>>,
    shared_solar_path_node_sets: Option<Arc<SharedSolarPathNodeSets>>,
    shared_solar_path_samples: Option<Arc<SharedSolarPathSamples>>,
    shared_solar_path_sample_sets: Option<Arc<SharedSolarPathSampleSets>>,
    solar_ray_diagnostics: Option<Arc<SolarRayDiagnostics>>,
    shared_motion_index: Option<usize>,
    shared_solar_path_sample_index: Option<usize>,
    shared_solar_path_sample_set_index: Option<usize>,
    shared_solar_path_sample_set_bearing_bits: u32,
    shared_solar_path_bearing_matches: bool,
    shared_solar_path_aerosol_enabled: bool,
    observer_path_batch_enabled: bool,
    angular_footprint_cache_enabled: bool,
    solar_path_template_cache_enabled: bool,
    solar_path_exact_cache_enabled: bool,
    unified_projection_sort_enabled: bool,
    audit_records: Vec<PathAuditRecord>,
    projections: Vec<CloudProjection>,
    ledger_projections: Vec<CloudProjection>,
    pending_solar_cells: Vec<PendingSolarCell>,
    layer_cache: Vec<CloudLayerSample>,
    layer_cache_generation: Vec<u32>,
    terrain_cache: Vec<f32>,
    terrain_cache_generation: Vec<u32>,
    aerosol_column_cache: Vec<AerosolColumn>,
    aerosol_column_cache_generation: Vec<u32>,
    solar_horizontal_path: Vec<HorizontalPathNode>,
    observer_horizontal_path: Vec<HorizontalPathNode>,
    observer_path_slab_nodes: Vec<HorizontalPathNode>,
    observer_path_slab_spans: Vec<ObserverPathSpan>,
    observer_path_midpoint_slab: Vec<ObserverMidpointGeometry>,
    observer_midpoint_path: Vec<ObserverMidpointGeometry>,
    observer_path_cloud_slab: Vec<[CloudLayerSample; 3]>,
    observer_path_cloud_generation: Vec<u32>,
    observer_path_results: Vec<[ObserverCloudBasePath; 3]>,
    observer_path_results_generation: Vec<u32>,
    observer_path_active_geometry: Option<usize>,
    observer_path_active_span: Option<ObserverPathSpan>,
    path_distance_trig: Vec<PathDistanceTrig>,
    solar_bearing_bits: u32,
    solar_bearing_sin: f32,
    solar_bearing_cos: f32,
    shared_solar_path_cell: Option<usize>,
    solar_path_geometry: Option<DestinationPathGeometry>,
    observer_path_geometry: Option<DestinationPathGeometry>,
    solar_path_cloud_layers: Vec<[CloudLayerSample; 3]>,
    solar_path_cloud_cache: HashMap<SolarPathCloudCacheKey, Vec<[CloudLayerSample; 3]>>,
    solar_path_cloud_cache_active_key: Option<SolarPathCloudCacheKey>,
    observer_path_cloud_layers: Vec<[CloudLayerSample; 3]>,
    solar_path_template_cache: HashMap<SolarPathTemplateKey, SolarPathTemplate>,
    solar_path_template_active_key: Option<SolarPathTemplateKey>,
    observer_geometry: Vec<ObserverCellGeometry>,
    observer_geometry_rows: Vec<ObserverGeometryRow>,
    observer_geometry_key: Option<(usize, f32, f32)>,
    observer_cell: usize,
    solar_aerosol_path_cache: Vec<AerosolPathCache>,
    observer_aerosol_path_cache: Vec<AerosolPathCache>,
    motion_layer_cache: Vec<CloudLayerSample>,
    motion_layer_cache_generation: Vec<u32>,
    motion_state_cache: Vec<u8>,
    motion_state_cache_generation: Vec<u32>,
    motion_wind_all_generation: Vec<u32>,
    motion_wind_cache: Vec<[f32; 4]>,
    motion_position_cache: Vec<[f32; 4]>,
    motion_position_cache_generation: Vec<u32>,
    motion_scalar_cache: Vec<f32>,
    motion_scalar_cache_generation: Vec<u32>,
    shared_wind_index: Option<usize>,
    layer_cache_key: Option<LayerCacheKey>,
    layer_cache_current_generation: u32,
    sky_view: Vec<f32>,
    sky_burn: Vec<f32>,
    sky_load: Vec<f32>,
    sky_material_load: Vec<f32>,
    sky_incoming_aerosol_load: Vec<f32>,
    observer_aerosol_optical_depth: Vec<f32>,
    observer_aerosol_transmission: Vec<f32>,
    layer_occupied: Vec<f32>,
    ledger_layer_occupied: Vec<f32>,
    ledger_view: Vec<f32>,
    ledger_target_eligible: Vec<f32>,
    ledger_geometry_reachable: Vec<f32>,
    ledger_external_path_transmission: Vec<f32>,
    unoccluded_burn_upper: Vec<f32>,
    layer_view: [Vec<f32>; 3],
    layer_burn: [Vec<f32>; 3],
    incoming_blocked: Vec<f32>,
    target_deck_connected: Vec<f32>,
    target_deck_attenuated: Vec<f32>,
    target_deep_risk: Vec<f32>,
    cloud_optical_depth: Vec<f32>,
    peak_blocking: Vec<f32>,
    clear_gap_count: Vec<f32>,
}

impl SkyBuffers {
    fn new() -> Self {
        Self {
            audit_enabled: false,
            aerosol_empty: false,
            aerosol_interpolation_fast_path_safe: false,
            shared_winds: None,
            shared_terrain: None,
            shared_motion: None,
            shared_solar_path_nodes: None,
            shared_solar_path_node_sets: None,
            shared_solar_path_samples: None,
            shared_solar_path_sample_sets: None,
            solar_ray_diagnostics: None,
            shared_motion_index: None,
            shared_solar_path_sample_index: None,
            shared_solar_path_sample_set_index: None,
            shared_solar_path_sample_set_bearing_bits: u32::MAX,
            shared_solar_path_bearing_matches: false,
            shared_solar_path_aerosol_enabled: false,
            observer_path_batch_enabled: std::env::var_os(
                "FIRESKY_NATIVE_DISABLE_OBSERVER_PATH_BATCH",
            )
            .is_none(),
            angular_footprint_cache_enabled: std::env::var_os(
                "FIRESKY_NATIVE_DISABLE_ANGULAR_FOOTPRINT_CACHE",
            )
            .is_none(),
            // The block/angle template is an approximation because it stores
            // absolute path cells. Keep it opt-in until it is replaced by an
            // exact relative-path cache.
            solar_path_template_cache_enabled: std::env::var_os(
                "FIRESKY_NATIVE_ENABLE_SOLAR_PATH_TEMPLATE_CACHE",
            )
            .is_some()
                && std::env::var_os("FIRESKY_NATIVE_DISABLE_SOLAR_PATH_TEMPLATE_CACHE").is_none(),
            solar_path_exact_cache_enabled: std::env::var_os(
                "FIRESKY_NATIVE_ENABLE_SOLAR_PATH_EXACT_CACHE",
            )
            .is_some()
                && std::env::var_os("FIRESKY_NATIVE_DISABLE_SOLAR_PATH_EXACT_CACHE").is_none(),
            // The unified ledger order lets the normal score path reuse the
            // candidate projection vector. Set the disable flag to retain
            // the legacy two-vector ordering for diagnostics or rollback.
            unified_projection_sort_enabled: std::env::var_os(
                "FIRESKY_NATIVE_DISABLE_UNIFIED_PROJECTION_SORT",
            )
            .is_none(),
            audit_records: Vec::new(),
            projections: Vec::new(),
            ledger_projections: Vec::new(),
            pending_solar_cells: Vec::new(),
            layer_cache: Vec::new(),
            layer_cache_generation: Vec::new(),
            terrain_cache: Vec::new(),
            terrain_cache_generation: Vec::new(),
            aerosol_column_cache: Vec::new(),
            aerosol_column_cache_generation: Vec::new(),
            solar_horizontal_path: Vec::new(),
            observer_horizontal_path: Vec::new(),
            observer_path_slab_nodes: Vec::new(),
            observer_path_slab_spans: Vec::new(),
            observer_path_midpoint_slab: Vec::new(),
            observer_midpoint_path: Vec::new(),
            observer_path_cloud_slab: Vec::new(),
            observer_path_cloud_generation: Vec::new(),
            observer_path_results: Vec::new(),
            observer_path_results_generation: Vec::new(),
            observer_path_active_geometry: None,
            observer_path_active_span: None,
            path_distance_trig: Vec::new(),
            solar_bearing_bits: u32::MAX,
            solar_bearing_sin: 0.0,
            solar_bearing_cos: 1.0,
            shared_solar_path_cell: None,
            solar_path_geometry: None,
            observer_path_geometry: None,
            solar_path_cloud_layers: Vec::new(),
            solar_path_cloud_cache: HashMap::new(),
            solar_path_cloud_cache_active_key: None,
            observer_path_cloud_layers: Vec::new(),
            solar_path_template_cache: HashMap::new(),
            solar_path_template_active_key: None,
            observer_geometry: Vec::new(),
            observer_geometry_rows: Vec::new(),
            observer_geometry_key: None,
            observer_cell: 0,
            solar_aerosol_path_cache: Vec::new(),
            observer_aerosol_path_cache: Vec::new(),
            motion_layer_cache: Vec::new(),
            motion_layer_cache_generation: Vec::new(),
            motion_state_cache: Vec::new(),
            motion_state_cache_generation: Vec::new(),
            motion_wind_all_generation: Vec::new(),
            motion_wind_cache: Vec::new(),
            motion_position_cache: Vec::new(),
            motion_position_cache_generation: Vec::new(),
            motion_scalar_cache: Vec::new(),
            motion_scalar_cache_generation: Vec::new(),
            shared_wind_index: None,
            layer_cache_key: None,
            layer_cache_current_generation: 0,
            sky_view: vec![0.0; VIEW_ANGULAR_CELLS],
            sky_burn: vec![0.0; VIEW_ANGULAR_CELLS],
            sky_load: vec![0.0; VIEW_ANGULAR_CELLS],
            sky_material_load: vec![0.0; VIEW_ANGULAR_CELLS],
            sky_incoming_aerosol_load: vec![0.0; VIEW_ANGULAR_CELLS],
            observer_aerosol_optical_depth: vec![0.0; VIEW_ANGULAR_CELLS],
            observer_aerosol_transmission: vec![0.0; VIEW_ANGULAR_CELLS],
            layer_occupied: vec![0.0; 3 * VIEW_ANGULAR_CELLS],
            ledger_layer_occupied: vec![0.0; 3 * VIEW_ANGULAR_CELLS],
            ledger_view: vec![0.0; VIEW_ANGULAR_CELLS],
            ledger_target_eligible: vec![0.0; VIEW_ANGULAR_CELLS],
            ledger_geometry_reachable: vec![0.0; VIEW_ANGULAR_CELLS],
            ledger_external_path_transmission: vec![0.0; VIEW_ANGULAR_CELLS],
            unoccluded_burn_upper: vec![0.0; VIEW_ANGULAR_CELLS],
            layer_view: std::array::from_fn(|_| vec![0.0; VIEW_ANGULAR_CELLS]),
            layer_burn: std::array::from_fn(|_| vec![0.0; VIEW_ANGULAR_CELLS]),
            incoming_blocked: vec![0.0; VIEW_ANGULAR_CELLS],
            target_deck_connected: vec![0.0; VIEW_ANGULAR_CELLS],
            target_deck_attenuated: vec![0.0; VIEW_ANGULAR_CELLS],
            target_deep_risk: vec![0.0; VIEW_ANGULAR_CELLS],
            cloud_optical_depth: vec![0.0; VIEW_ANGULAR_CELLS],
            peak_blocking: vec![0.0; VIEW_ANGULAR_CELLS],
            clear_gap_count: vec![0.0; VIEW_ANGULAR_CELLS],
        }
    }

    fn with_shared_caches(
        shared_winds: Option<Arc<SharedWindCache>>,
        shared_terrain: Option<Arc<SharedTerrainCache>>,
        shared_motion: Option<Arc<SharedMotionCache>>,
        shared_solar_path_nodes: Option<Arc<SharedSolarPathNodes>>,
        shared_solar_path_node_sets: Option<Arc<SharedSolarPathNodeSets>>,
        shared_solar_path_samples: Option<Arc<SharedSolarPathSamples>>,
        shared_solar_path_sample_sets: Option<Arc<SharedSolarPathSampleSets>>,
        solar_ray_diagnostics: Option<Arc<SolarRayDiagnostics>>,
    ) -> Self {
        let mut buffers = Self::new();
        buffers.shared_winds = shared_winds;
        buffers.shared_terrain = shared_terrain;
        buffers.shared_motion = shared_motion;
        buffers.shared_solar_path_nodes = shared_solar_path_nodes;
        buffers.shared_solar_path_node_sets = shared_solar_path_node_sets;
        buffers.shared_solar_path_samples = shared_solar_path_samples;
        buffers.shared_solar_path_sample_sets = shared_solar_path_sample_sets;
        buffers.solar_ray_diagnostics = solar_ray_diagnostics;
        buffers
    }

    fn reset(&mut self, audit_enabled: bool) {
        self.audit_enabled = audit_enabled;
        self.begin_horizontal_path_cache();
        self.audit_records.clear();
        self.projections.clear();
        self.ledger_projections.clear();
        self.pending_solar_cells.clear();
        self.sky_view.fill(0.0);
        self.sky_burn.fill(0.0);
        self.sky_load.fill(0.0);
        self.sky_material_load.fill(0.0);
        self.sky_incoming_aerosol_load.fill(0.0);
        self.observer_aerosol_optical_depth.fill(0.0);
        self.observer_aerosol_transmission.fill(0.0);
        self.layer_occupied.fill(0.0);
        for layer in &mut self.layer_view {
            layer.fill(0.0);
        }
        for layer in &mut self.layer_burn {
            layer.fill(0.0);
        }
        self.ledger_layer_occupied.fill(0.0);
        self.ledger_view.fill(0.0);
        self.ledger_target_eligible.fill(0.0);
        self.ledger_geometry_reachable.fill(0.0);
        self.ledger_external_path_transmission.fill(0.0);
        self.unoccluded_burn_upper.fill(0.0);
        self.incoming_blocked.fill(0.0);
        self.target_deck_connected.fill(0.0);
        self.target_deck_attenuated.fill(0.0);
        self.target_deep_risk.fill(0.0);
        self.cloud_optical_depth.fill(0.0);
        self.peak_blocking.fill(0.0);
        self.clear_gap_count.fill(0.0);
    }

    fn begin_observer_cache(&mut self) {
        self.begin_horizontal_path_cache();
        self.observer_geometry.clear();
        self.observer_geometry_rows.clear();
        self.observer_path_slab_nodes.clear();
        self.observer_path_slab_spans.clear();
        self.observer_path_midpoint_slab.clear();
        self.observer_midpoint_path.clear();
        self.observer_path_cloud_slab.clear();
        self.observer_path_cloud_generation.clear();
        self.observer_path_results.clear();
        self.observer_path_results_generation.clear();
        self.observer_geometry_key = None;
        self.observer_cell = 0;
        self.shared_motion_index = None;
        self.shared_solar_path_sample_index = None;
        self.shared_solar_path_sample_set_index = None;
        if !self.solar_path_exact_cache_enabled {
            self.solar_path_template_cache.clear();
        }
        if self.shared_solar_path_nodes.is_none() && self.shared_solar_path_node_sets.is_none() {
            self.solar_path_cloud_cache.clear();
        }
        self.solar_path_cloud_cache_active_key = None;
        self.solar_path_template_active_key = None;
    }

    fn begin_horizontal_path_cache(&mut self) {
        self.finish_observer_path_slab();
        self.shared_solar_path_cell = None;
        self.shared_solar_path_bearing_matches = false;
        self.shared_solar_path_aerosol_enabled = false;
        self.shared_solar_path_sample_set_index = None;
        self.shared_solar_path_sample_set_bearing_bits = u32::MAX;
        self.solar_path_cloud_cache_active_key = None;
        self.solar_path_geometry = None;
        if self.solar_path_template_active_key.is_none() {
            self.solar_horizontal_path.clear();
            self.solar_path_cloud_layers.clear();
        }
        self.solar_aerosol_path_cache.clear();
        self.observer_horizontal_path.clear();
        self.observer_path_geometry = None;
        self.observer_path_cloud_layers.clear();
        self.observer_aerosol_path_cache.clear();
    }

    fn begin_observer_geometry_path(&mut self, geometry_index: usize) {
        self.begin_horizontal_path_cache();
        self.observer_path_active_geometry = Some(geometry_index);
        self.observer_path_active_span = self
            .observer_path_slab_spans
            .get(geometry_index)
            .copied()
            .filter(|span| span.len > 0);
    }

    fn finish_observer_path_slab(&mut self) {
        let Some(geometry_index) = self.observer_path_active_geometry.take() else {
            self.observer_path_active_span = None;
            self.observer_horizontal_path.clear();
            self.observer_midpoint_path.clear();
            return;
        };
        if self.observer_path_active_span.is_none() && !self.observer_horizontal_path.is_empty() {
            if let Some(span) = self.observer_path_slab_spans.get_mut(geometry_index) {
                let start = self.observer_path_slab_nodes.len();
                self.observer_midpoint_path.resize(
                    self.observer_horizontal_path.len(),
                    ObserverMidpointGeometry::default(),
                );
                self.observer_path_slab_nodes
                    .extend_from_slice(&self.observer_horizontal_path);
                self.observer_path_midpoint_slab
                    .extend_from_slice(&self.observer_midpoint_path);
                let computed_cloud_len = self
                    .observer_path_cloud_layers
                    .len()
                    .min(self.observer_horizontal_path.len());
                self.observer_path_cloud_layers.resize(
                    self.observer_horizontal_path.len(),
                    [CloudLayerSample::default(); 3],
                );
                self.observer_path_cloud_slab
                    .extend_from_slice(&self.observer_path_cloud_layers);
                self.observer_path_cloud_generation.extend(
                    (0..self.observer_horizontal_path.len()).map(|index| {
                        (index < computed_cloud_len)
                            .then_some(self.layer_cache_current_generation)
                            .unwrap_or(0)
                    }),
                );
                *span = ObserverPathSpan {
                    start,
                    len: self.observer_horizontal_path.len(),
                };
            }
        }
        self.observer_path_active_span = None;
        self.observer_horizontal_path.clear();
        self.observer_midpoint_path.clear();
    }

    fn observer_horizontal_path_node(&self, index: usize) -> Option<HorizontalPathNode> {
        if let Some(span) = self.observer_path_active_span {
            return (index < span.len)
                .then(|| self.observer_path_slab_nodes.get(span.start + index))
                .flatten()
                .copied();
        }
        self.observer_horizontal_path.get(index).copied()
    }

    fn observer_midpoint_geometry(&self, index: usize) -> Option<ObserverMidpointGeometry> {
        if let Some(span) = self.observer_path_active_span {
            return self
                .observer_path_midpoint_slab
                .get(span.start.saturating_add(index))
                .copied();
        }
        self.observer_midpoint_path.get(index).copied()
    }

    fn store_observer_midpoint_geometry(
        &mut self,
        index: usize,
        geometry: ObserverMidpointGeometry,
    ) {
        if let Some(span) = self.observer_path_active_span {
            let absolute_index = span.start.saturating_add(index);
            if self.observer_path_midpoint_slab.len() <= absolute_index {
                self.observer_path_midpoint_slab
                    .resize(absolute_index + 1, ObserverMidpointGeometry::default());
            }
            self.observer_path_midpoint_slab[absolute_index] = geometry;
        } else {
            if self.observer_midpoint_path.len() <= index {
                self.observer_midpoint_path
                    .resize(index + 1, ObserverMidpointGeometry::default());
            }
            self.observer_midpoint_path[index] = geometry;
        }
    }

    fn materialize_observer_path_slab(&mut self) {
        let Some(span) = self.observer_path_active_span.take() else {
            return;
        };
        if let Some(nodes) = self
            .observer_path_slab_nodes
            .get(span.start..span.start.saturating_add(span.len))
        {
            self.observer_horizontal_path.extend_from_slice(nodes);
        }
        if let Some(midpoints) = self
            .observer_path_midpoint_slab
            .get(span.start..span.start.saturating_add(span.len))
        {
            self.observer_midpoint_path.extend_from_slice(midpoints);
        }
    }

    fn prepare_observer_path_slab(&mut self, geometry_len: usize) {
        self.observer_path_slab_nodes.clear();
        self.observer_path_midpoint_slab.clear();
        self.observer_path_slab_spans.clear();
        self.observer_path_slab_spans
            .resize(geometry_len, ObserverPathSpan::default());
        self.observer_path_results
            .resize(geometry_len, [ObserverCloudBasePath::default(); 3]);
        self.observer_path_results_generation
            .resize(geometry_len, 0);
    }

    fn cached_observer_path_results(
        &self,
        geometry_index: usize,
    ) -> Option<[ObserverCloudBasePath; 3]> {
        (self
            .observer_path_results_generation
            .get(geometry_index)
            .copied()
            == Some(self.layer_cache_current_generation))
        .then(|| self.observer_path_results.get(geometry_index).copied())
        .flatten()
    }

    fn store_observer_path_results(
        &mut self,
        geometry_index: usize,
        paths: [ObserverCloudBasePath; 3],
    ) {
        if self.observer_path_results.len() <= geometry_index {
            self.observer_path_results
                .resize(geometry_index + 1, [ObserverCloudBasePath::default(); 3]);
            self.observer_path_results_generation
                .resize(geometry_index + 1, 0);
        }
        self.observer_path_results[geometry_index] = paths;
        self.observer_path_results_generation[geometry_index] = self.layer_cache_current_generation;
    }

    fn cached_path_distance_trig(&mut self, index: usize, distance_km: f32) -> PathDistanceTrig {
        let distance_bits = distance_km.to_bits();
        if let Some(cached) = self.path_distance_trig.get(index).copied() {
            if cached.distance_bits == distance_bits {
                return cached;
            }
        }
        let angular_distance = distance_km / EARTH_RADIUS_KM;
        let (sin, cos) = angular_distance.sin_cos();
        let computed = PathDistanceTrig {
            distance_bits,
            sin,
            cos,
            tangent: sin / cos,
        };
        if self.path_distance_trig.len() <= index {
            self.path_distance_trig.resize(
                index + 1,
                PathDistanceTrig {
                    distance_bits: u32::MAX,
                    sin: 0.0,
                    cos: 1.0,
                    tangent: 0.0,
                },
            );
        }
        self.path_distance_trig[index] = computed;
        computed
    }

    fn cached_solar_bearing_trig(&mut self, bearing_deg: f32) -> (f32, f32) {
        let bearing_bits = bearing_deg.to_bits();
        if self.solar_bearing_bits != bearing_bits {
            let bearing_rad = bearing_deg.to_radians();
            let (sin, cos) = bearing_rad.sin_cos();
            self.solar_bearing_bits = bearing_bits;
            self.solar_bearing_sin = sin;
            self.solar_bearing_cos = cos;
        }
        (self.solar_bearing_sin, self.solar_bearing_cos)
    }

    fn invalidate_solar_path_template(&mut self) {
        self.solar_horizontal_path.clear();
        self.solar_path_cloud_layers.clear();
        self.solar_path_template_active_key = None;
        self.solar_path_geometry = None;
    }

    #[cfg(test)]
    fn begin_layer_cache(&mut self, cell_count: usize) {
        self.begin_layer_cache_with_key(cell_count, None);
    }

    #[cfg(test)]
    fn begin_layer_cache_for_sample(
        &mut self,
        cell_count: usize,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
    ) {
        let key = temporal_cache_key(
            left,
            right,
            weight,
            left_seconds,
            right_seconds,
            *TEMPORAL_CACHE_MODE,
        );
        self.begin_layer_cache_for_key(cell_count, key);
    }

    fn begin_layer_cache_for_key(&mut self, cell_count: usize, key: LayerCacheKey) {
        self.begin_layer_cache_with_key(cell_count, Some(key));
        self.shared_motion_index = self
            .shared_motion
            .as_ref()
            .and_then(|cache| cache.index(key));
        self.shared_solar_path_sample_index = self
            .shared_solar_path_samples
            .as_ref()
            .and_then(|cache| cache.index(key));
        if self.shared_solar_path_sample_index.is_none() {
            self.shared_solar_path_sample_index = self
                .shared_solar_path_sample_sets
                .as_ref()
                .and_then(|cache| cache.key_index(key));
        }
        self.shared_wind_index = self.shared_winds.as_ref().and_then(|cache| {
            cache.index(WindCacheKey {
                left: key.left,
                right: key.right,
                left_enabled: f32::from_bits(key.left_seconds_bits) > 0.0
                    && f32::from_bits(key.left_seconds_bits).is_finite(),
                right_enabled: f32::from_bits(key.right_seconds_bits) > 0.0
                    && f32::from_bits(key.right_seconds_bits).is_finite(),
            })
        });
    }

    fn begin_layer_cache_with_key(&mut self, cell_count: usize, key: Option<LayerCacheKey>) {
        self.shared_wind_index = None;
        let required = cell_count.saturating_mul(3);
        let mut resized = false;
        if self.layer_cache.len() != required {
            self.layer_cache
                .resize(required, CloudLayerSample::default());
            self.layer_cache_generation.resize(required, 0);
            self.layer_cache_current_generation = 0;
            resized = true;
        }
        if self.aerosol_column_cache.len() != cell_count {
            self.aerosol_column_cache.resize(
                cell_count,
                AerosolColumn {
                    aod550: 0.0,
                    extinction_m_inv: [0.0; AEROSOL_PROFILE_LEVELS],
                },
            );
            self.aerosol_column_cache_generation.resize(cell_count, 0);
            self.layer_cache_current_generation = 0;
            resized = true;
        }
        if self.terrain_cache.len() != cell_count {
            self.terrain_cache.resize(cell_count, 0.0);
            self.terrain_cache_generation.resize(cell_count, 0);
            self.layer_cache_current_generation = 0;
            resized = true;
        }
        if self.motion_layer_cache.len() != required {
            self.motion_layer_cache
                .resize(required, CloudLayerSample::default());
            self.motion_layer_cache_generation.resize(required, 0);
            self.motion_state_cache.resize(required, 0);
            self.motion_state_cache_generation.resize(required, 0);
            self.motion_wind_cache.resize(required, [0.0; 4]);
            self.motion_position_cache.resize(required, [0.0; 4]);
            self.motion_position_cache_generation.resize(required, 0);
            let scalar_required = required.saturating_mul(2);
            self.motion_scalar_cache.resize(scalar_required, 0.0);
            self.motion_scalar_cache_generation
                .resize(scalar_required, 0);
            self.layer_cache_current_generation = 0;
            resized = true;
        }
        if self.motion_wind_all_generation.len() != cell_count {
            self.motion_wind_all_generation.resize(cell_count, 0);
            self.layer_cache_current_generation = 0;
            resized = true;
        }
        if resized || key.is_none() || self.layer_cache_key != key {
            self.layer_cache_current_generation =
                self.layer_cache_current_generation.wrapping_add(1).max(1);
            self.layer_cache_key = key;
            if self.layer_cache_current_generation == 1 {
                self.layer_cache_generation.fill(0);
                self.terrain_cache_generation.fill(0);
                self.aerosol_column_cache_generation.fill(0);
                self.motion_layer_cache_generation.fill(0);
                self.motion_state_cache_generation.fill(0);
                self.motion_wind_all_generation.fill(0);
                self.motion_position_cache_generation.fill(0);
                self.motion_scalar_cache_generation.fill(0);
            }
        }
    }

    fn selected_shared_solar_path_samples(&self) -> Option<&SharedSolarPathSamples> {
        if let (Some(sets), Some(path_index)) = (
            self.shared_solar_path_sample_sets.as_ref(),
            self.shared_solar_path_sample_set_index,
        ) {
            return sets.paths.get(path_index);
        }
        self.shared_solar_path_samples.as_deref()
    }

    fn shared_solar_path_cloud_layers(
        &self,
        path_index: usize,
    ) -> Option<&[SharedCloudLayerSample; 3]> {
        if !self.shared_solar_path_bearing_matches {
            return None;
        }
        let cache = self.selected_shared_solar_path_samples()?;
        let key_index = self.shared_solar_path_sample_index?;
        let origin_cell = self.shared_solar_path_cell?;
        cache.cloud_layers_ref(key_index, origin_cell, path_index)
    }

    fn load_solar_path_template(&mut self, key: SolarPathTemplateKey) -> bool {
        if self.solar_path_template_active_key == Some(key)
            && !self.solar_horizontal_path.is_empty()
            && self.solar_path_cloud_layers.len() == self.solar_horizontal_path.len()
        {
            return true;
        }
        let Some(template) = self.solar_path_template_cache.get(&key) else {
            self.invalidate_solar_path_template();
            return false;
        };
        self.solar_horizontal_path.clone_from(&template.nodes);
        self.solar_path_cloud_layers
            .clone_from(&template.cloud_layers);
        self.solar_path_template_active_key = Some(key);
        true
    }

    fn store_solar_path_template(&mut self, key: SolarPathTemplateKey) {
        if self.solar_horizontal_path.is_empty()
            || self.solar_path_cloud_layers.len() != self.solar_horizontal_path.len()
            || self
                .solar_horizontal_path
                .last()
                .is_some_and(|node| node.cell.is_none())
        {
            return;
        }
        let max_entries = if key.exact {
            SOLAR_PATH_EXACT_CACHE_MAX_ENTRIES
        } else {
            SOLAR_PATH_TEMPLATE_MAX_ENTRIES
        };
        if self.solar_path_template_cache.len() >= max_entries
            && !self.solar_path_template_cache.contains_key(&key)
        {
            self.solar_path_template_cache.clear();
        }
        self.solar_path_template_cache.insert(
            key,
            SolarPathTemplate {
                nodes: self.solar_horizontal_path.clone(),
                cloud_layers: self.solar_path_cloud_layers.clone(),
            },
        );
        self.solar_path_template_active_key = Some(key);
    }

    fn load_solar_path_cloud_cache(&mut self, key: SolarPathCloudCacheKey) -> bool {
        let Some(cached) = self.solar_path_cloud_cache.get(&key) else {
            self.solar_path_cloud_cache_active_key = None;
            return false;
        };
        self.solar_path_cloud_layers.clone_from(cached);
        self.solar_path_cloud_cache_active_key = Some(key);
        true
    }

    fn store_solar_path_cloud_cache(&mut self, key: SolarPathCloudCacheKey, expected_nodes: usize) {
        if self.solar_path_cloud_cache_active_key.is_some()
            || self.solar_path_cloud_layers.len() != expected_nodes
        {
            // A path that terminated before reaching its nominal trace extent
            // is intentionally not stored. The current light result may have
            // used the early terrain/domain exit and is not a complete reusable
            // cloud-layer path.
            return;
        }
        let max_entries = std::env::var("FIRESKY_NATIVE_SOLAR_PATH_CLOUD_CACHE_ENTRIES")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(262_144);
        if self.solar_path_cloud_cache.len() >= max_entries
            && !self.solar_path_cloud_cache.contains_key(&key)
        {
            self.solar_path_cloud_cache.clear();
        }
        self.solar_path_cloud_cache
            .insert(key, self.solar_path_cloud_layers.clone());
        self.solar_path_cloud_cache_active_key = Some(key);
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct CloudLayerSample {
    amount: f32,
    opacity: f32,
    material_quality: f32,
    base_m: f32,
    top_m: f32,
}

#[derive(Clone, Copy, Default)]
struct SolarCloudBaseLight {
    cloud_transmission: f32,
    geometry_reachable: bool,
    path_cloud_transmission_before_target_self: f32,
    aerosol_optical_depth: f32,
    target_deck_connected: f32,
    target_deck_attenuated_fraction: f32,
    cloud_optical_depth: f32,
    peak_blocking: f32,
    clear_gap_count: f32,
    audit: PathAuditRecord,
}

impl SolarRayWork {
    #[allow(clippy::too_many_arguments)]
    fn from_solar_inputs(
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        target_lat: f32,
        target_lon: f32,
        target_distance_km: f32,
        target_sector_deg: f32,
        target_sector_cos: f32,
        active: [bool; 3],
        clouds: [CloudLayerSample; 3],
        target_deep_risks: [f32; 3],
        solar_elevation_deg: f32,
        solar_illumination_elevation_rad: f32,
        solar_azimuth_deg: f32,
        target_terrain_override: Option<f32>,
    ) -> Self {
        let active_mask = active
            .iter()
            .enumerate()
            .fold(0u32, |mask, (layer, active)| {
                mask | (u32::from(*active) << layer)
            });
        let flags = SOLAR_RAY_WORK_FLAG_EXACT
            | if target_terrain_override.is_some() {
                SOLAR_RAY_WORK_FLAG_TARGET_TERRAIN_OVERRIDE
            } else {
                0
            };
        Self {
            left: u32::try_from(left).expect("solar work left index exceeds u32"),
            right: u32::try_from(right).expect("solar work right index exceeds u32"),
            active_mask,
            flags,
            weight,
            left_seconds,
            right_seconds,
            target_lat,
            target_lon,
            target_distance_km,
            target_sector_deg,
            target_sector_cos,
            solar_elevation_deg,
            solar_illumination_elevation_rad,
            solar_azimuth_deg,
            target_terrain_override_m: target_terrain_override.unwrap_or(0.0),
            cloud_amount: clouds.map(|cloud| cloud.amount),
            cloud_opacity: clouds.map(|cloud| cloud.opacity),
            cloud_material_quality: clouds.map(|cloud| cloud.material_quality),
            cloud_base_m: clouds.map(|cloud| cloud.base_m),
            cloud_top_m: clouds.map(|cloud| cloud.top_m),
            target_deep_risk: target_deep_risks,
        }
    }

    fn evaluate_scalar(
        &self,
        batch: &Batch<'_>,
        buffers: &mut SkyBuffers,
    ) -> [SolarCloudBaseLight; 3] {
        batch.solar_cloud_base_lights_with_illumination(
            self.left as usize,
            self.right as usize,
            self.weight,
            self.left_seconds,
            self.right_seconds,
            self.target_lat,
            self.target_lon,
            self.target_distance_km,
            self.target_sector_deg,
            self.target_sector_cos,
            self.active_layers(),
            self.clouds(),
            self.target_deep_risk,
            self.solar_elevation_deg,
            self.solar_illumination_elevation_rad,
            self.solar_azimuth_deg,
            self.target_terrain_override(),
            buffers,
        )
    }
}

struct SolarPathTrace {
    trace_steps: u32,
    unique_cells: u32,
    revisited_cells: u32,
    domain_break: bool,
    ray_height_break: bool,
    seen_cells: [usize; 64],
    seen_cell_count: usize,
    last_cell: Option<usize>,
}

impl SolarPathTrace {
    fn record_cell(&mut self, cell: usize) {
        if self.seen_cells[..self.seen_cell_count].contains(&cell) {
            if self.last_cell != Some(cell) {
                self.revisited_cells += 1;
            }
        } else if self.seen_cell_count < self.seen_cells.len() {
            self.seen_cells[self.seen_cell_count] = cell;
            self.seen_cell_count += 1;
            self.unique_cells += 1;
        }
        self.last_cell = Some(cell);
    }
}

impl Default for SolarPathTrace {
    fn default() -> Self {
        Self {
            trace_steps: 0,
            unique_cells: 0,
            revisited_cells: 0,
            domain_break: false,
            ray_height_break: false,
            seen_cells: [usize::MAX; 64],
            seen_cell_count: 0,
            last_cell: None,
        }
    }
}

fn path_audit_record(
    trace: Option<&SolarPathTrace>,
    external_tau: f32,
    target_self_transmission: f32,
    peak_blocking: f32,
    geometry_reachable: bool,
    opaque_cutoff: bool,
    target_extension_count: usize,
    target_cell_index: Option<usize>,
    target_opacity: f32,
) -> PathAuditRecord {
    let Some(trace) = trace else {
        return PathAuditRecord::default();
    };
    let external_tau = external_tau.max(0.0);
    let target_self_tau = if target_self_transmission < 1.0 {
        -target_self_transmission.clamp(0.02, 1.0).ln()
    } else {
        0.0
    };
    PathAuditRecord {
        external_tau,
        target_self_tau,
        pre_peak_external_transmission: if geometry_reachable {
            (-external_tau).exp().clamp(0.0, 1.0)
        } else {
            0.0
        },
        peak_factor: (1.0 - PEAK_BLOCKING_TRANSMISSION_WEIGHT * clamp01(peak_blocking)).max(0.0),
        trace_steps: trace.trace_steps,
        unique_cells: trace.unique_cells,
        revisited_cells: trace.revisited_cells,
        domain_break: trace.domain_break,
        ray_height_break: trace.ray_height_break,
        opaque_cutoff,
        target_extension_count_max: target_extension_count as u32,
        target_cell_index,
        target_opacity: target_opacity.clamp(0.0, 1.0),
    }
}

#[derive(Clone, Copy, Default)]
struct VisibleCloudBaseLight {
    burnable_fraction: f32,
    observer_burnable_fraction: f32,
    incoming_aerosol_illumination: f32,
    observer_aerosol_contrast: f32,
    observer_aerosol_optical_depth: f32,
    observer_aerosol_transmission: f32,
}

#[derive(Clone, Copy)]
struct ObserverCloudBasePath {
    optical_depth: f32,
    cloud_transmission: f32,
    terrain_blocked: bool,
}

impl Default for ObserverCloudBasePath {
    fn default() -> Self {
        Self {
            optical_depth: 0.0,
            cloud_transmission: 1.0,
            terrain_blocked: false,
        }
    }
}

fn visible_cloud_base_light(
    external_light: SolarCloudBaseLight,
    observer_aerosol_optical_depth: f32,
    observer_cloud_transmission: f32,
    cloud: CloudLayerSample,
    _layer: usize,
    _distance_km: f32,
    cell_radius_km: f32,
    solar_elevation_deg: f32,
) -> VisibleCloudBaseLight {
    let (observer_aerosol_optical_depth, observer_aerosol_transmission) =
        normalized_observer_return_diagnostics(observer_aerosol_optical_depth);
    // With the Sun above the horizon, a ray from the visible cloud base first
    // traverses the target cloud itself. Thick St/Sc/Ac therefore cannot be
    // treated like a directly illuminated underside merely because the
    // external atmosphere is clear. Thin cirrus retains partial transmission.
    let self_transmission = if solar_elevation_deg <= 0.0 {
        1.0
    } else {
        let thickness_km = ((cloud.top_m - cloud.base_m).max(0.0) / 1000.0).max(0.05);
        let tangent = solar_elevation_deg.to_radians().tan().max(1e-4);
        let distance_to_top_km = thickness_km / tangent;
        let in_cloud_path_fraction =
            (distance_to_top_km / (2.0 * cell_radius_km.max(1.0))).clamp(0.0, 1.0);
        (-2.4 * cloud.opacity * in_cloud_path_fraction).exp()
    };
    let cloud_reach = clamp01(external_light.cloud_transmission * self_transmission);
    let observer_cloud_reach = clamp01(cloud_reach * clamp01(observer_cloud_transmission));
    // The two optical legs have different jobs. Incoming direct sunlight
    // determines whether the cloud base is illuminated. The return leg changes
    // the contrast of that illuminated cloud for the observer. Their raw
    // Beer-Lambert transmissions multiply, but their perceptual response curves
    // must not be conflated into a second direct-sun extinction test.
    let incoming_aerosol_illumination =
        fire_cloud_illumination_from_aerosol_tau(external_light.aerosol_optical_depth);
    let observer_aerosol_contrast =
        observer_cloud_contrast_from_aerosol_tau(observer_aerosol_optical_depth);
    VisibleCloudBaseLight {
        // The incoming aerosol response is carried by
        // ``incoming_aerosol_illumination`` and applied once in the load
        // term. Keep the geometric burnable area independent of that same
        // direct-beam factor; otherwise near-extinction paths lose area here
        // and then lose the identical light again in the Python scorer.
        burnable_fraction: cloud_reach,
        observer_burnable_fraction: observer_cloud_reach,
        incoming_aerosol_illumination: clamp01(incoming_aerosol_illumination),
        observer_aerosol_contrast: clamp01(observer_aerosol_contrast),
        observer_aerosol_optical_depth,
        observer_aerosol_transmission,
    }
}

fn low_cloud_target_is_eligible(
    external_light: SolarCloudBaseLight,
    observer_path: ObserverCloudBasePath,
    cloud: CloudLayerSample,
    observer_elevation_m: f32,
    distance_km: f32,
) -> bool {
    if distance_km <= 1.0e-3
        || !external_light.geometry_reachable
        || observer_path.terrain_blocked
        || observer_path.cloud_transmission <= 1.0e-4
        || !cloud.base_m.is_finite()
        || !cloud.top_m.is_finite()
        || cloud.top_m <= cloud.base_m
        || cloud.base_m <= observer_elevation_m
        || external_light.path_cloud_transmission_before_target_self <= 1.0e-4
    {
        return false;
    }
    // The observer leg uses the full spherical terrain-aware trace. This
    // midpoint check keeps the eligibility contract explicit for compact
    // cells and avoids reintroducing a range policy disguised as a constant.
    let line_of_sight_midpoint = spherical_line_of_sight_height_m(
        observer_elevation_m,
        cloud.base_m,
        distance_km,
        0.5 * distance_km,
    );
    if !line_of_sight_midpoint.is_finite() || line_of_sight_midpoint <= observer_elevation_m {
        return false;
    }
    // Target eligibility is geometric/visibility reachability. Cloud
    // self-shielding is applied by visible_cloud_base_light to burn/load and
    // must not use its internal transmission floor as a qualification rule.
    true
}

fn angular_solid_angle_field(occupied_solid_angle: f32) -> f32 {
    let visible_solid_angle =
        VIEW_AZIMUTH_BINS as f32 * (VIEW_ELEVATION_BINS as f32).to_radians().sin();
    clamp01(occupied_solid_angle / visible_solid_angle.max(1e-6))
}

#[derive(Default)]
struct AngularReduction {
    candidate_solid_angle: f32,
    target_eligible_solid_angle: f32,
    geometry_reachable_solid_angle: f32,
    external_path_transmission_solid_angle: f32,
    unoccluded_burn_upper_solid_angle: f32,
    field_view_solid_angle: f32,
    field_burn_solid_angle: f32,
    field_load_solid_angle: f32,
    field_material_load_solid_angle: f32,
    field_incoming_aerosol_load_solid_angle: f32,
    high_view_solid_angle: f32,
    mid_view_solid_angle: f32,
    low_view_solid_angle: f32,
    high_burn_solid_angle: f32,
    mid_burn_solid_angle: f32,
    low_burn_solid_angle: f32,
    incoming_blocked_solid_angle: f32,
    weighted_visible: f32,
    observer_aerosol_optical_depth_weighted_sum: f32,
    observer_aerosol_transmission_weighted_sum: f32,
    target_deck_connected_weighted_sum: f32,
    target_deck_attenuated_weighted_sum: f32,
    target_deep_risk_weighted_sum: f32,
    cloud_optical_depth_weighted_sum: f32,
    peak_blocking_weighted_sum: f32,
    clear_gap_count_weighted_sum: f32,
}

fn reduce_angular_fields(buffers: &SkyBuffers) -> AngularReduction {
    let mut reduction = AngularReduction::default();
    for index in 0..VIEW_ANGULAR_CELLS {
        let elevation_bin = index % VIEW_ELEVATION_BINS;
        let solid_angle_weight = VIEW_ELEVATION_SOLID_ANGLE_WEIGHTS[elevation_bin];

        reduction.candidate_solid_angle += clamp01(buffers.ledger_view[index]) * solid_angle_weight;
        reduction.target_eligible_solid_angle +=
            clamp01(buffers.ledger_target_eligible[index]) * solid_angle_weight;
        reduction.geometry_reachable_solid_angle +=
            clamp01(buffers.ledger_geometry_reachable[index]) * solid_angle_weight;
        reduction.external_path_transmission_solid_angle +=
            clamp01(buffers.ledger_external_path_transmission[index]) * solid_angle_weight;
        reduction.unoccluded_burn_upper_solid_angle +=
            clamp01(buffers.unoccluded_burn_upper[index]) * solid_angle_weight;
        reduction.field_view_solid_angle += clamp01(buffers.sky_view[index]) * solid_angle_weight;
        reduction.field_burn_solid_angle += clamp01(buffers.sky_burn[index]) * solid_angle_weight;
        reduction.field_load_solid_angle += clamp01(buffers.sky_load[index]) * solid_angle_weight;
        reduction.field_material_load_solid_angle +=
            clamp01(buffers.sky_material_load[index]) * solid_angle_weight;
        reduction.field_incoming_aerosol_load_solid_angle +=
            clamp01(buffers.sky_incoming_aerosol_load[index]) * solid_angle_weight;
        reduction.high_view_solid_angle +=
            clamp01(buffers.layer_view[HIGH][index]) * solid_angle_weight;
        reduction.mid_view_solid_angle +=
            clamp01(buffers.layer_view[MID][index]) * solid_angle_weight;
        reduction.low_view_solid_angle +=
            clamp01(buffers.layer_view[LOW][index]) * solid_angle_weight;
        reduction.high_burn_solid_angle +=
            clamp01(buffers.layer_burn[HIGH][index]) * solid_angle_weight;
        reduction.mid_burn_solid_angle +=
            clamp01(buffers.layer_burn[MID][index]) * solid_angle_weight;
        reduction.low_burn_solid_angle +=
            clamp01(buffers.layer_burn[LOW][index]) * solid_angle_weight;
        reduction.incoming_blocked_solid_angle +=
            clamp01(buffers.incoming_blocked[index]) * solid_angle_weight;

        let visible_weight = buffers.sky_view[index].max(0.0) * solid_angle_weight;
        reduction.weighted_visible += visible_weight;
        reduction.observer_aerosol_optical_depth_weighted_sum +=
            buffers.observer_aerosol_optical_depth[index].max(0.0) * solid_angle_weight;
        reduction.observer_aerosol_transmission_weighted_sum +=
            buffers.observer_aerosol_transmission[index].max(0.0) * solid_angle_weight;
        reduction.target_deck_connected_weighted_sum +=
            buffers.target_deck_connected[index].max(0.0) * solid_angle_weight;
        reduction.target_deck_attenuated_weighted_sum +=
            buffers.target_deck_attenuated[index].max(0.0) * solid_angle_weight;
        reduction.target_deep_risk_weighted_sum +=
            buffers.target_deep_risk[index].max(0.0) * solid_angle_weight;
        reduction.cloud_optical_depth_weighted_sum +=
            buffers.cloud_optical_depth[index].max(0.0) * solid_angle_weight;
        reduction.peak_blocking_weighted_sum +=
            buffers.peak_blocking[index].max(0.0) * solid_angle_weight;
        reduction.clear_gap_count_weighted_sum +=
            buffers.clear_gap_count[index].max(0.0) * solid_angle_weight;
    }
    reduction
}

fn conditional_weighted_mean(weighted_sum: f32, visible_weight: f32) -> f32 {
    if weighted_sum.is_finite() && visible_weight.is_finite() && visible_weight > 1e-6 {
        weighted_sum / visible_weight
    } else {
        0.0
    }
}

fn dominant_burnable_pressure_family_index(family_burn: [f32; 3]) -> Option<usize> {
    let index = if family_burn[0] >= family_burn[1] && family_burn[0] >= family_burn[2] {
        0
    } else if family_burn[1] >= family_burn[2] {
        1
    } else {
        2
    };
    (family_burn[index] >= DOMINANT_BURNABLE_PRESSURE_FAMILY_MIN_AMOUNT).then_some(index)
}

#[cfg(test)]
fn angular_cloud_coverage(opacity: &[u8; VIEW_ANGULAR_CELLS]) -> f32 {
    let mut occupied_solid_angle = 0.0f32;
    for azimuth_bin in 0..VIEW_AZIMUTH_BINS {
        for elevation_bin in 0..VIEW_ELEVATION_BINS {
            let index = azimuth_bin * VIEW_ELEVATION_BINS + elevation_bin;
            let lower = (elevation_bin as f32).to_radians();
            let upper = ((elevation_bin + 1) as f32).to_radians();
            let solid_angle_weight = upper.sin() - lower.sin();
            occupied_solid_angle +=
                opacity[index] as f32 / VIEW_OPACITY_MAX as f32 * solid_angle_weight;
        }
    }
    let visible_solid_angle =
        VIEW_AZIMUTH_BINS as f32 * (VIEW_ELEVATION_BINS as f32).to_radians().sin();
    (occupied_solid_angle / visible_solid_angle.max(1e-6)).clamp(0.0, 1.0)
}

fn smooth_gate(value: f32, low: f32, high: f32) -> f32 {
    if value <= low {
        return 0.0;
    }
    if value >= high {
        return 1.0;
    }
    let t = (value - low) / (high - low);
    t * t * (3.0 - 2.0 * t)
}

/// Standard atmospheric refraction folds into the curvature term as a 7/6
/// effective Earth radius (k = 1/7): at 200 km the bare-geometry formula
/// overestimated the drop by ~450 m, i.e. ~0.13 degrees of low-cloud
/// elevation.
fn apparent_elevation(distance: f32, height: f32) -> f32 {
    const EFFECTIVE_RADIUS_KM: f32 = 6371.0 * (7.0 / 6.0);
    let curvature = distance * distance * 1000.0 / (2.0 * EFFECTIVE_RADIUS_KM);
    (height - curvature)
        .atan2((distance * 1000.0).max(1.0))
        .to_degrees()
}

/// Elevation bins cover 0..90 degrees, but the refracted sun disk sits below
/// the geometric horizon at sunset, so clouds slightly below the geometric
/// horizon still occlude it. Bin 0 extends down to this floor instead of
/// discarding such footprints outright.
const VIEW_SUB_HORIZON_FLOOR_DEG: f32 = -1.5;

fn target_point(lat: f32, lon: f32, bearing: f32, distance_km: f32) -> (f32, f32) {
    // Match the Python geometry path: the ray can span hundreds of
    // kilometres, so a local equirectangular offset accumulates a material
    // longitude/latitude error at the edge of the 1000 km field.
    DestinationPathGeometry::new(lat, lon, bearing).target_point(distance_km)
}

fn longitude_on_grid(lon: f32, lon0: f32, spacing: f32, cols: usize) -> f32 {
    let end = lon0 + spacing * cols.saturating_sub(1) as f32;
    if lon0 < -180.0 || end > 180.0 || (end == 180.0 && lon0 > -180.0) {
        lon + 360.0 * (((lon0 + end) * 0.5 - lon) / 360.0).round()
    } else if lon == 180.0 && lon0 == -180.0 && end < 180.0 {
        -180.0
    } else {
        lon
    }
}

fn cell_for_point(
    lat: f32,
    lon: f32,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    rows: usize,
    cols: usize,
) -> Option<usize> {
    let lon = longitude_on_grid(lon, lon0, spacing, cols);
    let row = ((lat - lat0) / spacing).round() as isize;
    let col = ((lon - lon0) / spacing).round() as isize;
    if row < 0 || col < 0 || row >= rows as isize || col >= cols as isize {
        None
    } else {
        Some(row as usize * cols + col as usize)
    }
}

struct Batch<'a> {
    grid: &'a [f32],
    aerosol_profiles: &'a [f32],
    time_count: usize,
    cell_count: usize,
    observers: &'a [f32],
    samples: &'a [f32],
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    observer_geometry_templates: Option<ObserverGeometryTemplates>,
}

impl Batch<'_> {
    fn build_shared_solar_path_nodes(&self) -> Option<Arc<SharedSolarPathNodes>> {
        if std::env::var_os("FIRESKY_NATIVE_DISABLE_SHARED_SOLAR_PATH_CACHE").is_some()
            || self.observers.len() / OBSERVER_FIELDS < 128
            || self.cell_count < 128
            || self.cell_count > u32::MAX as usize
            || self.rows == 0
            || self.cols == 0
            || !self.spacing.is_finite()
            || self.spacing <= 0.0
            || !self.radius_km.is_finite()
            || self.radius_km <= 0.0
        {
            return None;
        }

        let mut bearing_bits = None;
        for sample in self.samples.chunks_exact(SAMPLE_FIELDS) {
            if sample[SAMPLE_VALID] < 0.5 {
                continue;
            }
            let bearing = sample[1];
            if !bearing.is_finite() {
                return None;
            }
            let bits = bearing.to_bits();
            if bearing_bits.is_some_and(|known| known != bits) {
                return None;
            }
            bearing_bits = Some(bits);
        }
        let bearing_bits = bearing_bits?;
        let bearing_deg = f32::from_bits(bearing_bits);
        let step_km = (self.spacing.abs() * 111.0).clamp(8.0, 35.0);
        let max_trace_km = 1000.0f32.min(self.radius_km + 400.0);
        let nodes_per_cell = (max_trace_km / step_km).ceil() as usize + 1;
        let distance_table =
            shared_solar_path_distance_table(step_km, max_trace_km, nodes_per_cell);
        let total_nodes = self.cell_count.checked_mul(nodes_per_cell)?;
        let estimated_bytes =
            total_nodes.checked_mul(std::mem::size_of::<SharedHorizontalPathNode>())?;
        let max_bytes = std::env::var("FIRESKY_NATIVE_SHARED_SOLAR_PATH_MAX_BYTES")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(2 * 1024 * 1024 * 1024);
        if estimated_bytes > max_bytes {
            return None;
        }

        let bearing_rad = bearing_deg.to_radians();
        let (bearing_sin, bearing_cos) = bearing_rad.sin_cos();
        let default_node = HorizontalPathNode {
            distance_km: 0.0,
            sample_tangent: 0.0,
            cell: None,
            horizontal: [(0, 0.0); 4],
        };
        let mut nodes = vec![default_node; total_nodes];
        nodes
            .par_chunks_mut(nodes_per_cell)
            .enumerate()
            .for_each(|(cell, output)| {
                let row = cell / self.cols;
                let col = cell % self.cols;
                let origin_lat = self.lat0 + row as f32 * self.spacing;
                let origin_lon = self.lon0 + col as f32 * self.spacing;
                let origin = DestinationPathOrigin::from_lat_lon(origin_lat, origin_lon);
                let geometry = DestinationPathGeometry::from_origin_with_bearing_trig(
                    origin,
                    bearing_sin,
                    bearing_cos,
                );
                for (index, node) in output.iter_mut().enumerate() {
                    let (distance_km, distance_trig) = distance_table[index];
                    let (lat, lon) = if distance_km == 0.0 {
                        (origin_lat, origin_lon)
                    } else {
                        geometry.target_point_with_trig(distance_trig.sin, distance_trig.cos)
                    };
                    let resolved_cell = if index == 0 {
                        Some(cell)
                    } else {
                        cell_for_point(
                            lat,
                            lon,
                            self.lat0,
                            self.lon0,
                            self.spacing,
                            self.rows,
                            self.cols,
                        )
                    };
                    *node = HorizontalPathNode {
                        distance_km,
                        sample_tangent: distance_trig.tangent,
                        cell: resolved_cell,
                        horizontal: self.horizontal_weights(lat, lon),
                    };
                }
            });
        let nodes = nodes
            .into_iter()
            .map(SharedHorizontalPathNode::from_node)
            .collect();
        let distances_km = distance_table
            .iter()
            .map(|(distance_km, _)| *distance_km)
            .collect();
        let sample_tangents = distance_table
            .iter()
            .map(|(_, distance_trig)| distance_trig.tangent)
            .collect();
        Some(Arc::new(SharedSolarPathNodes {
            bearing_bits,
            nodes_per_cell,
            distances_km,
            sample_tangents,
            nodes,
        }))
    }

    fn build_shared_solar_path_nodes_for_bearing(
        &self,
        bearing_bits: u32,
        nodes_per_cell: usize,
        distance_table: &[(f32, PathDistanceTrig)],
    ) -> SharedSolarPathNodes {
        let total_nodes = self.cell_count * nodes_per_cell;
        let bearing_deg = f32::from_bits(bearing_bits);
        let bearing_rad = bearing_deg.to_radians();
        let (bearing_sin, bearing_cos) = bearing_rad.sin_cos();
        let default_node = HorizontalPathNode {
            distance_km: 0.0,
            sample_tangent: 0.0,
            cell: None,
            horizontal: [(0, 0.0); 4],
        };
        let mut nodes = vec![default_node; total_nodes];
        nodes
            .par_chunks_mut(nodes_per_cell)
            .enumerate()
            .for_each(|(cell, output)| {
                let row = cell / self.cols;
                let col = cell % self.cols;
                let origin_lat = self.lat0 + row as f32 * self.spacing;
                let origin_lon = self.lon0 + col as f32 * self.spacing;
                let origin = DestinationPathOrigin::from_lat_lon(origin_lat, origin_lon);
                let geometry = DestinationPathGeometry::from_origin_with_bearing_trig(
                    origin,
                    bearing_sin,
                    bearing_cos,
                );
                for (index, node) in output.iter_mut().enumerate() {
                    let (distance_km, distance_trig) = distance_table[index];
                    let (lat, lon) = if distance_km == 0.0 {
                        (origin_lat, origin_lon)
                    } else {
                        geometry.target_point_with_trig(distance_trig.sin, distance_trig.cos)
                    };
                    let resolved_cell = if index == 0 {
                        Some(cell)
                    } else {
                        cell_for_point(
                            lat,
                            lon,
                            self.lat0,
                            self.lon0,
                            self.spacing,
                            self.rows,
                            self.cols,
                        )
                    };
                    *node = HorizontalPathNode {
                        distance_km,
                        sample_tangent: distance_trig.tangent,
                        cell: resolved_cell,
                        horizontal: self.horizontal_weights(lat, lon),
                    };
                }
            });
        let nodes = nodes
            .into_iter()
            .map(SharedHorizontalPathNode::from_node)
            .collect();
        let distances_km = distance_table
            .iter()
            .map(|(distance_km, _)| *distance_km)
            .collect();
        let sample_tangents = distance_table
            .iter()
            .map(|(_, distance_trig)| distance_trig.tangent)
            .collect();
        SharedSolarPathNodes {
            bearing_bits,
            nodes_per_cell,
            distances_km,
            sample_tangents,
            nodes,
        }
    }

    fn build_shared_solar_path_node_sets(&self) -> Option<Arc<SharedSolarPathNodeSets>> {
        const MAX_BEARINGS: usize = 16;

        if std::env::var_os("FIRESKY_NATIVE_DISABLE_SHARED_SOLAR_PATH_CACHE").is_some()
            || std::env::var_os("FIRESKY_NATIVE_DISABLE_MULTI_BEARING_PATH_CACHE").is_some()
            || self.observers.len() / OBSERVER_FIELDS
                < std::env::var("FIRESKY_NATIVE_MULTI_BEARING_PATH_MIN_OBSERVERS")
                    .ok()
                    .and_then(|value| value.parse::<usize>().ok())
                    .filter(|value| *value > 0)
                    .unwrap_or(2048)
            || self.cell_count < 128
            || self.cell_count > u32::MAX as usize
            || self.rows == 0
            || self.cols == 0
            || !self.spacing.is_finite()
            || self.spacing <= 0.0
            || !self.radius_km.is_finite()
            || self.radius_km <= 0.0
        {
            return None;
        }

        let mut bearings = Vec::new();
        for sample in self.samples.chunks_exact(SAMPLE_FIELDS) {
            if sample[SAMPLE_VALID] < 0.5 {
                continue;
            }
            let bearing = sample[1];
            if !bearing.is_finite() {
                return None;
            }
            let bits = bearing.to_bits();
            if !bearings.contains(&bits) {
                bearings.push(bits);
                if bearings.len() > MAX_BEARINGS {
                    return None;
                }
            }
        }
        if bearings.len() <= 1 {
            return None;
        }

        let step_km = (self.spacing.abs() * 111.0).clamp(8.0, 35.0);
        let max_trace_km = 1000.0f32.min(self.radius_km + 400.0);
        let nodes_per_cell = (max_trace_km / step_km).ceil() as usize + 1;
        let distance_table =
            shared_solar_path_distance_table(step_km, max_trace_km, nodes_per_cell);
        let total_nodes = self.cell_count.checked_mul(nodes_per_cell)?;
        let per_path_bytes =
            total_nodes.checked_mul(std::mem::size_of::<SharedHorizontalPathNode>())?;
        let estimated_bytes = per_path_bytes.checked_mul(bearings.len())?;
        let max_bytes = std::env::var("FIRESKY_NATIVE_SHARED_SOLAR_PATH_MULTI_MAX_BYTES")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(4 * 1024 * 1024 * 1024);
        if estimated_bytes > max_bytes {
            return None;
        }

        let paths = bearings
            .into_par_iter()
            .map(|bearing_bits| {
                self.build_shared_solar_path_nodes_for_bearing(
                    bearing_bits,
                    nodes_per_cell,
                    &distance_table,
                )
            })
            .collect();
        Some(Arc::new(SharedSolarPathNodeSets { paths }))
    }

    fn build_shared_solar_path_samples(
        &self,
        shared_nodes: Option<&Arc<SharedSolarPathNodes>>,
        shared_motion: Option<&Arc<SharedMotionCache>>,
        shared_terrain: Option<&Arc<SharedTerrainCache>>,
        aerosol_empty: bool,
        cancel: &BatchCancel,
    ) -> Option<Arc<SharedSolarPathSamples>> {
        let observer_count = self.observers.len() / OBSERVER_FIELDS;
        let min_observers = std::env::var("FIRESKY_NATIVE_SHARED_SOLAR_PATH_SAMPLE_MIN_OBSERVERS")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(SHARED_SOLAR_PATH_SAMPLE_MIN_OBSERVERS);
        if std::env::var_os("FIRESKY_NATIVE_DISABLE_SHARED_SOLAR_PATH_SAMPLE_CACHE").is_some()
            || observer_count < min_observers
            || self.cell_count < 128
        {
            return None;
        }
        let nodes = shared_nodes?;
        let motion = shared_motion?;
        let max_bytes = std::env::var("FIRESKY_NATIVE_SHARED_SOLAR_PATH_SAMPLE_MAX_BYTES")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(8 * 1024 * 1024 * 1024);
        let samples = self.build_shared_solar_path_samples_for_nodes(
            nodes,
            motion,
            shared_terrain,
            aerosol_empty,
            max_bytes,
            cancel,
        )?;
        Some(Arc::new(samples))
    }

    fn build_shared_solar_path_sample_sets(
        &self,
        shared_node_sets: Option<&Arc<SharedSolarPathNodeSets>>,
        shared_motion: Option<&Arc<SharedMotionCache>>,
        shared_terrain: Option<&Arc<SharedTerrainCache>>,
        aerosol_empty: bool,
        cancel: &BatchCancel,
    ) -> Option<Arc<SharedSolarPathSampleSets>> {
        let observer_count = self.observers.len() / OBSERVER_FIELDS;
        let min_observers =
            std::env::var("FIRESKY_NATIVE_SHARED_SOLAR_PATH_MULTI_SAMPLE_MIN_OBSERVERS")
                .ok()
                .and_then(|value| value.parse::<usize>().ok())
                .filter(|value| *value > 0)
                .or_else(|| {
                    std::env::var("FIRESKY_NATIVE_SHARED_SOLAR_PATH_SAMPLE_MIN_OBSERVERS")
                        .ok()
                        .and_then(|value| value.parse::<usize>().ok())
                        .filter(|value| *value > 0)
                })
                .unwrap_or(SHARED_SOLAR_PATH_MULTI_SAMPLE_MIN_OBSERVERS);
        if std::env::var_os("FIRESKY_NATIVE_DISABLE_SHARED_SOLAR_PATH_SAMPLE_CACHE").is_some()
            || std::env::var_os("FIRESKY_NATIVE_DISABLE_MULTI_BEARING_PATH_SAMPLE_CACHE").is_some()
            || observer_count < min_observers
            || self.cell_count < 128
        {
            return None;
        }
        let node_sets = shared_node_sets?;
        let motion = shared_motion?;
        let cache_aerosol = aerosol_empty
            || std::env::var_os("FIRESKY_NATIVE_ENABLE_MULTI_BEARING_PATH_AEROSOL_SAMPLE_CACHE")
                .is_some();
        if node_sets.paths.len() <= 1
            || (cache_aerosol && !aerosol_empty && shared_terrain.is_none())
        {
            // Aerosol normalization depends on the exact terrain used by the
            // scorer. Refuse the shared path table when that invariant is not
            // available instead of silently changing the optical integral.
            return None;
        }
        let max_bytes = std::env::var("FIRESKY_NATIVE_SHARED_SOLAR_PATH_MULTI_SAMPLE_MAX_BYTES")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .or_else(|| {
                std::env::var("FIRESKY_NATIVE_SHARED_SOLAR_PATH_SAMPLE_MAX_BYTES")
                    .ok()
                    .and_then(|value| value.parse::<usize>().ok())
                    .filter(|value| *value > 0)
            })
            .unwrap_or(8 * 1024 * 1024 * 1024);
        let nodes_per_cell = node_sets.paths.first()?.nodes_per_cell;
        let total_nodes = self.cell_count.checked_mul(nodes_per_cell)?;
        let sample_aerosol_empty = aerosol_empty || !cache_aerosol;
        let bytes_per_node = std::mem::size_of::<[SharedCloudLayerSample; 3]>()
            + std::mem::size_of::<[SharedCloudLayerSample; 3]>()
            + if sample_aerosol_empty {
                0
            } else {
                std::mem::size_of::<AerosolPathCache>()
            };
        let bytes_per_key = total_nodes.checked_mul(bytes_per_node)?;
        let key_count = motion.keys.len();
        let bytes_per_path = bytes_per_key.checked_mul(key_count)?;
        let estimated_bytes = bytes_per_path.checked_mul(node_sets.paths.len())?;
        if bytes_per_key == 0
            || key_count == 0
            || bytes_per_path > max_bytes
            || estimated_bytes > max_bytes
        {
            return None;
        }

        // Build one bearing at a time. Each bearing uses Rayon internally for
        // its keys and path nodes, while sequential bearing construction keeps
        // the temporary per-key allocations from multiplying the peak RSS.
        let mut paths = Vec::with_capacity(node_sets.paths.len());
        for nodes in &node_sets.paths {
            if cancel.is_cancelled() {
                return None;
            }
            paths.push(self.build_shared_solar_path_samples_for_nodes(
                nodes,
                motion,
                shared_terrain,
                sample_aerosol_empty,
                max_bytes,
                cancel,
            )?);
        }
        let bearing_indices = paths
            .iter()
            .enumerate()
            .map(|(index, path)| (path.bearing_bits, index))
            .collect();
        Some(Arc::new(SharedSolarPathSampleSets {
            paths,
            bearing_indices,
        }))
    }

    fn build_shared_solar_path_samples_for_nodes(
        &self,
        nodes: &SharedSolarPathNodes,
        motion: &SharedMotionCache,
        shared_terrain: Option<&Arc<SharedTerrainCache>>,
        aerosol_empty: bool,
        max_bytes: usize,
        cancel: &BatchCancel,
    ) -> Option<SharedSolarPathSamples> {
        if !aerosol_empty && shared_terrain.is_none() {
            return None;
        }
        let nodes_per_cell = nodes.nodes_per_cell;
        let total_nodes = self.cell_count.checked_mul(nodes_per_cell)?;
        let bytes_per_node = std::mem::size_of::<[SharedCloudLayerSample; 3]>()
            + if aerosol_empty {
                0
            } else {
                std::mem::size_of::<AerosolPathCache>()
            };
        let bytes_per_key = total_nodes.checked_mul(bytes_per_node)?;
        let key_count = motion.keys.len();
        if bytes_per_key == 0 || key_count == 0 || bytes_per_key.checked_mul(key_count)? > max_bytes
        {
            return None;
        }

        let aborted = AtomicBool::new(false);
        let per_key: Vec<
            Option<(
                LayerCacheKey,
                Vec<[SharedCloudLayerSample; 3]>,
                Option<Vec<AerosolPathCache>>,
            )>,
        > = motion
            .keys
            .par_iter()
            .enumerate()
            .map(|(key_index, key)| {
                if aborted.load(AtomicOrdering::Relaxed) || cancel.is_cancelled() {
                    aborted.store(true, AtomicOrdering::Relaxed);
                    return None;
                }
                let mut cloud_layers = vec![[SharedCloudLayerSample::default(); 3]; total_nodes];
                let weight = f32::from_bits(key.weight_bits);
                let mut aerosol_paths =
                    (!aerosol_empty).then(|| vec![AerosolPathCache::default(); total_nodes]);
                let aerosol_columns = (!aerosol_empty).then(|| {
                    (0..self.cell_count)
                        .into_par_iter()
                        .map(|cell| self.temporal_aerosol_column(key.left, key.right, weight, cell))
                        .collect::<Vec<_>>()
                });
                let terrain = shared_terrain;
                cloud_layers
                    .par_chunks_mut(nodes_per_cell)
                    .enumerate()
                    .for_each(|(cell, output)| {
                        if cancel.is_cancelled() {
                            aborted.store(true, AtomicOrdering::Relaxed);
                            return;
                        }
                        for (path_index, value) in output.iter_mut().enumerate() {
                            let Some(node) = nodes.node(cell, path_index) else {
                                continue;
                            };
                            let layers = motion
                                .layers_for_horizontal(key_index, self.cell_count, &node.horizontal)
                                .unwrap_or_default();
                            *value = layers.map(SharedCloudLayerSample::from_cloud);
                        }
                    });
                if let (Some(paths), Some(columns), Some(terrain)) =
                    (aerosol_paths.as_mut(), aerosol_columns.as_ref(), terrain)
                {
                    paths
                        .par_chunks_mut(nodes_per_cell)
                        .enumerate()
                        .for_each(|(cell, output)| {
                            if cancel.is_cancelled() {
                                aborted.store(true, AtomicOrdering::Relaxed);
                                return;
                            }
                            for (path_index, value) in output.iter_mut().enumerate() {
                                let Some(node) = nodes.node(cell, path_index) else {
                                    continue;
                                };
                                let Some(path_cell) = node.cell else {
                                    continue;
                                };
                                let column = interpolated_aerosol_column_from_columns(
                                    columns,
                                    &node.horizontal,
                                );
                                let terrain_m = terrain.values[path_cell];
                                let support = AerosolColumn::support(terrain_m);
                                let valid = column.is_valid();
                                let normalization = if valid && column.aod550 != 0.0 {
                                    column.normalization_integral(terrain_m)
                                } else {
                                    0.0
                                };
                                *value = AerosolPathCache {
                                    column,
                                    normalization,
                                    support,
                                    valid,
                                };
                            }
                        });
                }
                Some((*key, cloud_layers, aerosol_paths))
            })
            .collect::<Vec<_>>();
        if aborted.load(AtomicOrdering::Relaxed) || cancel.is_cancelled() {
            return None;
        }
        let mut keys = Vec::with_capacity(per_key.len());
        let mut cloud_layers = Vec::with_capacity(per_key.len());
        let mut aerosol_layers = (!aerosol_empty).then(|| Vec::with_capacity(key_count));
        for entry in per_key {
            let (key, layers, paths) = entry?;
            keys.push(key);
            cloud_layers.push(layers);
            if let (Some(aerosol_layers), Some(paths)) = (aerosol_layers.as_mut(), paths) {
                aerosol_layers.push(paths);
            }
        }
        let indices = keys
            .iter()
            .enumerate()
            .map(|(index, key)| (*key, index))
            .collect();
        Some(SharedSolarPathSamples {
            bearing_bits: nodes.bearing_bits,
            nodes_per_cell,
            indices,
            cloud_layers,
            aerosol_paths: aerosol_layers,
        })
    }

    fn build_shared_wind_cache(&self) -> Option<Arc<SharedWindCache>> {
        if std::env::var_os("FIRESKY_NATIVE_DISABLE_SHARED_WIND_CACHE").is_some()
            || self.observers.len() / OBSERVER_FIELDS < 32
            || self.cell_count < 128
        {
            return None;
        }

        let mut keys = Vec::new();
        let mut seen_keys = HashSet::new();
        for sample in self.samples.chunks_exact(SAMPLE_FIELDS) {
            if sample[SAMPLE_VALID] < 0.5 {
                continue;
            }
            let left_seconds = sample[SAMPLE_LEFT_SECONDS].max(0.0);
            let right_seconds = sample[SAMPLE_RIGHT_SECONDS].max(0.0);
            let key = WindCacheKey {
                left: sample[2].max(0.0) as usize,
                right: sample[3].max(0.0) as usize,
                left_enabled: left_seconds > 0.0 && left_seconds.is_finite(),
                right_enabled: right_seconds > 0.0 && right_seconds.is_finite(),
            };
            if seen_keys.insert(key) {
                keys.push(key);
            }
        }
        if keys.is_empty() {
            return None;
        }

        let bytes_per_key = self
            .cell_count
            .saturating_mul(3)
            .saturating_mul(std::mem::size_of::<[f32; 4]>());
        const MAX_SHARED_WIND_CACHE_BYTES: usize = 128 * 1024 * 1024;
        if bytes_per_key.saturating_mul(keys.len()) > MAX_SHARED_WIND_CACHE_BYTES {
            return None;
        }

        // Independent per-cell interpolation: build each key's dense wind
        // table in parallel (same values as the sequential reference loop).
        let values: Vec<Vec<[f32; 4]>> = keys
            .par_iter()
            .map(|key| {
                (0..self.cell_count)
                    .into_par_iter()
                    .with_min_len(512)
                    .flat_map_iter(|cell| {
                        let row = cell / self.cols;
                        let col = cell % self.cols;
                        let lat = self.lat0 + row as f32 * self.spacing;
                        let lon = self.lon0 + col as f32 * self.spacing;
                        let horizontal = if key.left_enabled || key.right_enabled {
                            self.cloud_horizontal_weights(lat, lon)
                        } else {
                            None
                        };
                        (0..3).map(move |layer| {
                            let (u_field, v_field) = match layer {
                                0 => (HIGH_U, HIGH_V),
                                1 => (MID_U, MID_V),
                                _ => (LOW_U, LOW_V),
                            };
                            let wind_at = |time: usize| {
                                let Some(horizontal) = horizontal.as_ref() else {
                                    return (0.0, 0.0);
                                };
                                let u = self.horizontal_at_time(time, horizontal, u_field);
                                let v = self.horizontal_at_time(time, horizontal, v_field);
                                if u.is_finite() && v.is_finite() {
                                    (u, v)
                                } else {
                                    (0.0, 0.0)
                                }
                            };
                            let (left_u, left_v) = if key.left_enabled {
                                wind_at(key.left)
                            } else {
                                (0.0, 0.0)
                            };
                            let (right_u, right_v) = if key.right_enabled {
                                wind_at(key.right)
                            } else {
                                (0.0, 0.0)
                            };
                            [left_u, left_v, right_u, right_v]
                        })
                    })
                    .collect()
            })
            .collect();

        let indices = keys
            .iter()
            .enumerate()
            .map(|(index, key)| (*key, index))
            .collect();
        Some(Arc::new(SharedWindCache { indices, values }))
    }

    fn build_shared_motion_cache(&self, cancel: &BatchCancel) -> Option<Arc<SharedMotionCache>> {
        if std::env::var_os("FIRESKY_NATIVE_DISABLE_SHARED_MOTION_CACHE").is_some()
            || self.observers.len() / OBSERVER_FIELDS < 32
            || self.cell_count < 128
            || self.cols == 0
        {
            return None;
        }

        let bytes_per_key = self
            .cell_count
            .saturating_mul(3)
            .saturating_mul(std::mem::size_of::<CloudLayerSample>())
            .saturating_add(
                self.cell_count
                    .saturating_mul(6)
                    .saturating_mul(std::mem::size_of::<f32>()),
            );
        // The dense table is the main cross-observer raytrace reuse. The
        // The fixed China dusk input has 195 temporal keys (~2.21 GiB at 84
        // bytes per key/cell). Dawn, another cycle, or a wider bracket can
        // differ. This limit bounds only the table, not total process memory;
        // acceptance still measures physical footprint including worker
        // caches and outputs.
        const MAX_SHARED_MOTION_CACHE_BYTES: usize = 3 * 1024 * 1024 * 1024;
        let max_cacheable_keys = MAX_SHARED_MOTION_CACHE_BYTES / bytes_per_key;
        let diagnose_keys = std::env::var_os("FIRESKY_NATIVE_DIAG_SHARED_MOTION_KEYS").is_some();
        let mut keys = Vec::new();
        let mut seen_keys = HashSet::new();
        for sample in self.samples.chunks_exact(SAMPLE_FIELDS) {
            if sample[SAMPLE_VALID] < 0.5 {
                continue;
            }
            let key = temporal_cache_key(
                sample[2].max(0.0) as usize,
                sample[3].max(0.0) as usize,
                clamp01(sample[4]),
                sample[SAMPLE_LEFT_SECONDS].max(0.0),
                sample[SAMPLE_RIGHT_SECONDS].max(0.0),
                *TEMPORAL_CACHE_MODE,
            );
            if seen_keys.insert(key) {
                keys.push(key);
                if !diagnose_keys && keys.len() > max_cacheable_keys {
                    return None;
                }
            }
        }
        if keys.is_empty() {
            return None;
        }

        let estimated_dense_bytes = bytes_per_key.saturating_mul(keys.len());
        if diagnose_keys {
            let valid_sample_count = self
                .samples
                .chunks_exact(SAMPLE_FIELDS)
                .filter(|sample| sample[SAMPLE_VALID] >= 0.5)
                .count();
            eprintln!(
                "shared_motion_keys valid_samples={} unique_keys={} duplicate_samples={} bytes_per_key={} estimated_dense_bytes={} budget_bytes={}",
                valid_sample_count,
                keys.len(),
                valid_sample_count.saturating_sub(keys.len()),
                bytes_per_key,
                estimated_dense_bytes,
                MAX_SHARED_MOTION_CACHE_BYTES,
            );
        }
        if estimated_dense_bytes > MAX_SHARED_MOTION_CACHE_BYTES {
            return None;
        }

        // Fill disjoint key slots in the final dense table directly. The
        // former Option-valued intermediate doubled this multi-GiB structure
        // during construction, and full-grid per-worker SkyBuffers added
        // another large mostly-unused working set. Stateless per-cell sampling
        // keeps the same arithmetic/order without those worker caches.
        let aborted = AtomicBool::new(false);
        let mut layers = vec![vec![[CloudLayerSample::default(); 3]; self.cell_count]; keys.len()];
        let mut scalars = vec![vec![[0.0f32; 6]; self.cell_count]; keys.len()];
        keys.par_iter()
            .zip(layers.par_iter_mut())
            .zip(scalars.par_iter_mut())
            .for_each(|((key, key_layers), key_scalars)| {
                if aborted.load(AtomicOrdering::Relaxed) || cancel.is_cancelled() {
                    aborted.store(true, AtomicOrdering::Relaxed);
                    return;
                }
                let weight = f32::from_bits(key.weight_bits);
                let left_seconds = f32::from_bits(key.left_seconds_bits);
                let right_seconds = f32::from_bits(key.right_seconds_bits);
                let aborted_cells = AtomicBool::new(false);
                key_layers
                    .par_iter_mut()
                    .zip(key_scalars.par_iter_mut())
                    .enumerate()
                    .with_min_len(256)
                    .for_each(|(cell, (cell_layers, cell_scalars))| {
                        if aborted_cells.load(AtomicOrdering::Relaxed) || cancel.is_cancelled() {
                            aborted_cells.store(true, AtomicOrdering::Relaxed);
                            return;
                        }
                        let (layers, scalars) = self.motion_cell_samples(
                            key.left,
                            key.right,
                            weight,
                            left_seconds,
                            right_seconds,
                            cell,
                        );
                        *cell_layers = layers;
                        *cell_scalars = scalars;
                    });
                if aborted_cells.load(AtomicOrdering::Relaxed) || cancel.is_cancelled() {
                    aborted.store(true, AtomicOrdering::Relaxed);
                }
            });
        if aborted.load(AtomicOrdering::Relaxed) || cancel.is_cancelled() {
            return None;
        }

        let indices = keys
            .iter()
            .enumerate()
            .map(|(index, key)| (*key, index))
            .collect();
        Some(Arc::new(SharedMotionCache {
            keys,
            indices,
            layers,
            scalars,
        }))
    }

    fn build_shared_terrain_cache(&self) -> Option<Arc<SharedTerrainCache>> {
        if std::env::var_os("FIRESKY_NATIVE_DISABLE_SHARED_TERRAIN_CACHE").is_some()
            || self.observers.len() / OBSERVER_FIELDS < 32
            || self.cell_count < 128
            || self.time_count == 0
        {
            return None;
        }

        let mut values = Vec::with_capacity(self.cell_count);
        for cell in 0..self.cell_count {
            let value = self.value(0, cell, TERRAIN_ELEVATION);
            if !value.is_finite() || value.to_bits() == (-0.0f32).to_bits() {
                return None;
            }
            if (1..self.time_count)
                .any(|time| self.value(time, cell, TERRAIN_ELEVATION).to_bits() != value.to_bits())
            {
                return None;
            }
            values.push(value);
        }
        Some(Arc::new(SharedTerrainCache { values }))
    }

    fn solar_cache_bucket(value: f32, width: f32) -> Option<i32> {
        if !value.is_finite() || !width.is_finite() || width <= 0.0 {
            return None;
        }
        Some(
            (value as f64 / width as f64)
                .round()
                .clamp(i32::MIN as f64, i32::MAX as f64) as i32,
        )
    }

    fn solar_path_template_key(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        target_lat: f32,
        target_lon: f32,
        bearing_deg: f32,
        target_terrain_override_m: Option<f32>,
    ) -> Option<(SolarPathTemplateKey, f32)> {
        if target_terrain_override_m.is_some()
            || !bearing_deg.is_finite()
            || !weight.is_finite()
            || !left_seconds.is_finite()
            || !right_seconds.is_finite()
        {
            return None;
        }
        let target_cell = cell_for_point(
            target_lat,
            target_lon,
            self.lat0,
            self.lon0,
            self.spacing,
            self.rows,
            self.cols,
        )?;
        let row = target_cell / self.cols.max(1);
        let col = target_cell % self.cols.max(1);
        let block_cols = self.cols.saturating_add(SOLAR_PATH_TEMPLATE_BLOCK_SIZE - 1)
            / SOLAR_PATH_TEMPLATE_BLOCK_SIZE;
        let target_block = (row / SOLAR_PATH_TEMPLATE_BLOCK_SIZE) * block_cols
            + col / SOLAR_PATH_TEMPLATE_BLOCK_SIZE;
        let bucket_count = (360.0 / SOLAR_PATH_TEMPLATE_ANGLE_BIN_DEG).round() as i32;
        let bearing_bucket = ((bearing_deg.rem_euclid(360.0) / SOLAR_PATH_TEMPLATE_ANGLE_BIN_DEG)
            .round() as i32)
            .rem_euclid(bucket_count);
        let path_bearing =
            (bearing_bucket as f32 * SOLAR_PATH_TEMPLATE_ANGLE_BIN_DEG).rem_euclid(360.0);
        let left_time_slice =
            (left_seconds as f64 / SOLAR_PATH_TEMPLATE_TIME_SLICE_SECONDS).round() as i64;
        let right_time_slice =
            (right_seconds as f64 / SOLAR_PATH_TEMPLATE_TIME_SLICE_SECONDS).round() as i64;
        let time_slice = left_time_slice
            .saturating_mul(1_000_000)
            .saturating_add(right_time_slice);
        let weight_bucket = Self::solar_cache_bucket(weight, SOLAR_PATH_TEMPLATE_WEIGHT_BIN)?;
        Some((
            SolarPathTemplateKey {
                left,
                right,
                time_slice,
                weight_bucket,
                target_block,
                bearing_bucket,
                exact_target_cell: 0,
                exact_bearing_bits: bearing_deg.to_bits(),
                exact_weight_bits: 0,
                exact_left_seconds_bits: 0,
                exact_right_seconds_bits: 0,
                exact: false,
            },
            path_bearing,
        ))
    }

    fn solar_path_exact_key(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        target_lat: f32,
        target_lon: f32,
        bearing_deg: f32,
        target_terrain_override_m: Option<f32>,
    ) -> Option<(SolarPathTemplateKey, f32)> {
        if target_terrain_override_m.is_some()
            || !weight.is_finite()
            || !left_seconds.is_finite()
            || !right_seconds.is_finite()
            || !bearing_deg.is_finite()
        {
            return None;
        }
        let target_cell = cell_for_point(
            target_lat,
            target_lon,
            self.lat0,
            self.lon0,
            self.spacing,
            self.rows,
            self.cols,
        )?;
        Some((
            SolarPathTemplateKey {
                left,
                right,
                time_slice: 0,
                weight_bucket: 0,
                target_block: target_cell,
                bearing_bucket: 0,
                exact_target_cell: target_cell,
                exact_bearing_bits: bearing_deg.to_bits(),
                exact_weight_bits: weight.to_bits(),
                exact_left_seconds_bits: left_seconds.to_bits(),
                exact_right_seconds_bits: right_seconds.to_bits(),
                exact: true,
            },
            bearing_deg,
        ))
    }

    fn solar_path_cloud_cache_key(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        target_lat: f32,
        target_lon: f32,
        bearing_deg: f32,
        target_terrain_override_m: Option<f32>,
        buffers: &SkyBuffers,
    ) -> Option<SolarPathCloudCacheKey> {
        // Shared path samples already provide the immutable cloud layer at
        // every node. Do not copy that same path into a per-thread hash map;
        // national batches rarely revisit an identical target key and would
        // otherwise spend substantial memory bandwidth cloning it.
        if buffers.shared_solar_path_samples.is_some()
            || buffers.shared_solar_path_sample_sets.is_some()
        {
            return None;
        }
        // The multi-bearing node table is shared, but its cloud layers are
        // still time-varying. Full path clones help small observer subsets
        // with high target reuse, while a national grid batch pays more hash
        // and memory traffic than it saves. Keep that mode opt-in.
        if buffers.shared_solar_path_node_sets.is_some() && !*MULTI_BEARING_CLOUD_CACHE_ENABLED {
            return None;
        }
        if (buffers.shared_solar_path_nodes.is_none()
            && buffers.shared_solar_path_node_sets.is_none())
            || buffers.shared_solar_path_cell.is_none()
            || target_terrain_override_m.is_some()
            || !weight.is_finite()
            || !left_seconds.is_finite()
            || !right_seconds.is_finite()
            || !bearing_deg.is_finite()
        {
            return None;
        }
        let target_cell = cell_for_point(
            target_lat,
            target_lon,
            self.lat0,
            self.lon0,
            self.spacing,
            self.rows,
            self.cols,
        )?;
        Some(SolarPathCloudCacheKey {
            layer: LayerCacheKey {
                left,
                right,
                weight_bits: weight.to_bits(),
                left_seconds_bits: left_seconds.to_bits(),
                right_seconds_bits: right_seconds.to_bits(),
            },
            target_cell,
            bearing_bits: bearing_deg.to_bits(),
        })
    }

    fn solar_path_node_count(&self) -> usize {
        let step_km = (self.spacing.abs() * 111.0).clamp(8.0, 35.0);
        let max_trace_km = 1000.0f32.min(self.radius_km + 400.0);
        (max_trace_km / step_km).ceil() as usize + 1
    }

    #[allow(clippy::too_many_arguments)]
    fn complete_solar_path_template(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        target_lat: f32,
        target_lon: f32,
        path_bearing: f32,
        buffers: &mut SkyBuffers,
    ) -> bool {
        let step_km = (self.spacing.abs() * 111.0).clamp(8.0, 35.0);
        let max_trace_km = 1000.0f32.min(self.radius_km + 400.0);
        let count = (max_trace_km / step_km).ceil() as usize;
        let expected_node_count = count.saturating_add(1);
        if buffers.solar_horizontal_path.is_empty()
            || buffers.solar_horizontal_path.len() > expected_node_count
        {
            return false;
        }

        for index in buffers.solar_horizontal_path.len()..expected_node_count {
            let distance_km = (index as f32 * step_km).min(max_trace_km);
            let node = self.cached_horizontal_path_node(
                buffers,
                true,
                index,
                distance_km,
                target_lat,
                target_lon,
                path_bearing,
            );
            if node.cell.is_none() {
                return false;
            }
        }

        if buffers.solar_path_cloud_layers.len() > buffers.solar_horizontal_path.len() {
            return false;
        }
        for index in buffers.solar_path_cloud_layers.len()..buffers.solar_horizontal_path.len() {
            let node = buffers.solar_horizontal_path[index];
            if node.cell.is_none() {
                return false;
            }
            self.cached_motion_cloud_layers_at_path_node(
                buffers,
                true,
                index,
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                &node.horizontal,
            );
        }

        buffers.solar_path_cloud_layers.len() == buffers.solar_horizontal_path.len()
    }

    fn populate_observer_geometry_from_template(
        &self,
        buffers: &mut SkyBuffers,
        observer_row: usize,
        observer_col: usize,
        obs_lat: f32,
        obs_lon: f32,
    ) -> bool {
        let Some(templates) = self.observer_geometry_templates.as_ref() else {
            return false;
        };
        let Some(template) = templates.by_observer_row.get(observer_row) else {
            return false;
        };
        let expected_lat = self.lat0 + observer_row as f32 * self.spacing;
        let expected_lon = self.lon0 + observer_col as f32 * self.spacing;
        if (obs_lat - expected_lat).abs() > 1e-4 || (obs_lon - expected_lon).abs() > 1e-4 {
            return false;
        }

        buffers.observer_cell = observer_row * self.cols + observer_col;
        for template_row in &template.rows {
            let row_start = buffers.observer_geometry.len();
            let mut first_col = None;
            for cell_template in &template.cells[template_row.start..template_row.end] {
                let target_col = observer_col as isize + cell_template.col_offset;
                if target_col < 0 || target_col >= self.cols as isize {
                    continue;
                }
                let target_col = target_col as usize;
                if first_col.is_none() {
                    first_col = Some(target_col);
                }
                let cell = template_row.target_row * self.cols + target_col;
                buffers.observer_geometry.push(ObserverCellGeometry {
                    cell,
                    lat: cell_template.lat,
                    lon: self.lon0 + target_col as f32 * self.spacing,
                    north_km: cell_template.north_km,
                    east_km: cell_template.east_km,
                    bearing_deg: cell_template.bearing_deg,
                    distance_km: cell_template.distance_km,
                    area_weight: cell_template.area_weight,
                    local_cell_radius_km: cell_template.local_cell_radius_km,
                });
            }
            if let Some(first_col) = first_col {
                buffers.observer_geometry_rows.push(ObserverGeometryRow {
                    start: row_start,
                    end: buffers.observer_geometry.len(),
                    first_col,
                });
            }
        }
        true
    }

    fn ensure_observer_geometry(
        &self,
        buffers: &mut SkyBuffers,
        observer_index: usize,
        obs_lat: f32,
        obs_lon: f32,
    ) {
        let key = (observer_index, obs_lat, obs_lon);
        if buffers.observer_geometry_key == Some(key) {
            return;
        }

        buffers.observer_geometry.clear();
        buffers.observer_geometry_rows.clear();
        buffers.prepare_observer_path_slab(0);
        buffers.observer_geometry_key = Some(key);
        if self.rows == 0 || self.cols == 0 || !self.spacing.is_finite() || self.spacing <= 0.0 {
            buffers.observer_cell = 0;
            return;
        }

        let observer_row = (((obs_lat - self.lat0) / self.spacing).round() as isize)
            .clamp(0, self.rows as isize - 1) as usize;
        let observer_col = (((obs_lon - self.lon0) / self.spacing).round() as isize)
            .clamp(0, self.cols as isize - 1) as usize;
        buffers.observer_cell = observer_row * self.cols + observer_col;

        if self.populate_observer_geometry_from_template(
            buffers,
            observer_row,
            observer_col,
            obs_lat,
            obs_lon,
        ) {
            buffers.prepare_observer_path_slab(buffers.observer_geometry.len());
            return;
        }

        let lat_radius = self.radius_km / 110.574;
        let lon_radius = self.radius_km / (111.320 * obs_lat.to_radians().cos().abs().max(0.2));
        let row_min = (((obs_lat - lat_radius - self.lat0) / self.spacing).floor() as isize)
            .clamp(0, self.rows as isize - 1) as usize;
        let row_max = (((obs_lat + lat_radius - self.lat0) / self.spacing).ceil() as isize)
            .clamp(0, self.rows as isize - 1) as usize;
        let col_min = (((obs_lon - lon_radius - self.lon0) / self.spacing).floor() as isize)
            .clamp(0, self.cols as isize - 1) as usize;
        let col_max = (((obs_lon + lon_radius - self.lon0) / self.spacing).ceil() as isize)
            .clamp(0, self.cols as isize - 1) as usize;
        let radius_sq = self.radius_km * self.radius_km;

        for row in row_min..=row_max {
            let lat = self.lat0 + row as f32 * self.spacing;
            let area_weight = lat.to_radians().cos().abs().max(0.2);
            let north_km = (lat - obs_lat) * 110.574;
            let row_bearing_cos = ((obs_lat + lat) * 0.5).to_radians().cos();
            let row_start = buffers.observer_geometry.len();
            let mut first_col = None;
            for col in col_min..=col_max {
                let lon = self.lon0 + col as f32 * self.spacing;
                let cell = row * self.cols + col;
                let east_km = (lon - obs_lon) * 111.320 * row_bearing_cos;
                let Some((_distance_sq, distance_km)) =
                    radius_distance_km(north_km, east_km, self.radius_km, radius_sq)
                else {
                    continue;
                };
                if first_col.is_none() {
                    first_col = Some(col);
                }
                buffers.observer_geometry.push(ObserverCellGeometry {
                    cell,
                    lat,
                    lon,
                    north_km,
                    east_km,
                    bearing_deg: east_km.atan2(north_km).to_degrees().rem_euclid(360.0),
                    distance_km,
                    area_weight,
                    local_cell_radius_km: local_cell_radius_km(self.spacing, lat),
                });
            }
            if let Some(first_col) = first_col {
                buffers.observer_geometry_rows.push(ObserverGeometryRow {
                    start: row_start,
                    end: buffers.observer_geometry.len(),
                    first_col,
                });
            }
        }
        buffers.prepare_observer_path_slab(buffers.observer_geometry.len());
    }

    fn value(&self, time: usize, cell: usize, field: usize) -> f32 {
        debug_assert!(time < self.time_count);
        debug_assert!(cell < self.cell_count);
        debug_assert!(field < GRID_FIELDS);
        let offset = (time * self.cell_count + cell) * GRID_FIELDS + field;
        // The public batch entry point validates the complete contiguous grid
        // shape before any worker starts. Internal callers clamp time indices
        // and use model field constants, so this removes a repeated bounds
        // check from the cloud and wind sampling hot path without changing
        // the address or the f32 value being read.
        unsafe { *self.grid.get_unchecked(offset) }
    }

    fn cached_path_origin(&self, lat: f32, lon: f32) -> Option<(usize, DestinationPathOrigin)> {
        let cell = cell_for_point(
            lat,
            lon,
            self.lat0,
            self.lon0,
            self.spacing,
            self.rows,
            self.cols,
        )?;
        let origin = self
            .observer_geometry_templates
            .as_ref()?
            .path_origins
            .get(cell)
            .copied()?;
        (origin.lat_bits == lat.to_bits() && origin.lon_bits == lon.to_bits())
            .then_some((cell, origin))
    }

    fn interpolated(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        cell: usize,
        field: usize,
    ) -> f32 {
        let a = self.value(left.min(self.time_count - 1), cell, field);
        let b = self.value(right.min(self.time_count - 1), cell, field);
        if a.is_nan() {
            b
        } else if b.is_nan() {
            a
        } else {
            a + (b - a) * weight
        }
    }

    fn cached_terrain_elevation(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        cell: usize,
    ) -> f32 {
        if let Some(value) = buffers
            .shared_terrain
            .as_ref()
            .and_then(|cache| cache.values.get(cell))
        {
            return *value;
        }
        if cell >= buffers.terrain_cache.len() || cell >= buffers.terrain_cache_generation.len() {
            return self.interpolated(left, right, weight, cell, TERRAIN_ELEVATION);
        }
        let generation = buffers.layer_cache_current_generation;
        if buffers.terrain_cache_generation[cell] != generation {
            buffers.terrain_cache[cell] =
                self.interpolated(left, right, weight, cell, TERRAIN_ELEVATION);
            buffers.terrain_cache_generation[cell] = generation;
        }
        buffers.terrain_cache[cell]
    }

    /// Return bilinear weights for a geographic sample on the regular model
    /// grid. The observer-field projection still uses discrete GFS cells, but
    /// light-path quantities are continuous functions of the ray location;
    /// selecting one nearest cell made a sub-kilometre move across a cell
    /// midpoint change the aerosol/cloud path discontinuously.
    fn horizontal_weights(&self, lat: f32, lon: f32) -> [(usize, f32); 4] {
        let lon = longitude_on_grid(lon, self.lon0, self.spacing, self.cols);
        self.horizontal_weights_on_grid(lat, lon)
    }

    /// Bilinear weights for a longitude already mapped onto the contiguous
    /// model axis. Callers that also need bounds checks can normalize once and
    /// reuse the result without changing the floating-point operation order.
    fn horizontal_weights_on_grid(&self, lat: f32, lon: f32) -> [(usize, f32); 4] {
        if self.rows == 0 || self.cols == 0 || !self.spacing.is_finite() || self.spacing <= 0.0 {
            return [(0, 1.0), (0, 0.0), (0, 0.0), (0, 0.0)];
        }
        let row_position = (lat - self.lat0) / self.spacing;
        let col_position = (lon - self.lon0) / self.spacing;
        let row0 = row_position.floor().clamp(0.0, (self.rows - 1) as f32) as usize;
        let col0 = col_position.floor().clamp(0.0, (self.cols - 1) as f32) as usize;
        let row1 = (row0 + 1).min(self.rows - 1);
        let col1 = (col0 + 1).min(self.cols - 1);
        let row_weight = (row_position - row0 as f32).clamp(0.0, 1.0);
        let col_weight = (col_position - col0 as f32).clamp(0.0, 1.0);
        [
            (
                row0 * self.cols + col0,
                (1.0 - row_weight) * (1.0 - col_weight),
            ),
            (row0 * self.cols + col1, (1.0 - row_weight) * col_weight),
            (row1 * self.cols + col0, row_weight * (1.0 - col_weight)),
            (row1 * self.cols + col1, row_weight * col_weight),
        ]
    }

    fn cloud_horizontal_weights(&self, lat: f32, lon: f32) -> Option<[(usize, f32); 4]> {
        if self.rows == 0 || self.cols == 0 || !self.spacing.is_finite() || self.spacing <= 0.0 {
            return None;
        }
        let lon = longitude_on_grid(lon, self.lon0, self.spacing, self.cols);
        let max_lat = self.lat0 + self.spacing * (self.rows - 1) as f32;
        let max_lon = self.lon0 + self.spacing * (self.cols - 1) as f32;
        if !lat.is_finite()
            || !lon.is_finite()
            || lat < self.lat0
            || lat > max_lat
            || lon < self.lon0
            || lon > max_lon
        {
            return None;
        }
        Some(self.horizontal_weights_on_grid(lat, lon))
    }

    #[cfg(test)]
    fn interpolated_horizontal(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        lat: f32,
        lon: f32,
        field: usize,
    ) -> f32 {
        let horizontal = self.horizontal_weights(lat, lon);
        self.interpolated_with_horizontal(left, right, weight, &horizontal, field)
    }

    fn interpolated_with_horizontal(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        horizontal: &[(usize, f32); 4],
        field: usize,
    ) -> f32 {
        let mut value = 0.0f32;
        let mut total_weight = 0.0f32;
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 {
                continue;
            }
            let sample = self.interpolated(left, right, weight, cell, field);
            if sample.is_finite() {
                value += horizontal_weight * sample;
                total_weight += horizontal_weight;
            }
        }
        if total_weight > 0.0 {
            value / total_weight
        } else {
            0.0
        }
    }

    fn cached_horizontal_path_node(
        &self,
        buffers: &mut SkyBuffers,
        solar: bool,
        index: usize,
        distance_km: f32,
        origin_lat: f32,
        origin_lon: f32,
        bearing_deg: f32,
    ) -> HorizontalPathNode {
        if solar && index == 0 {
            let bearing_bits = bearing_deg.to_bits();
            if buffers.shared_solar_path_sample_set_bearing_bits != bearing_bits {
                buffers.shared_solar_path_sample_set_index = buffers
                    .shared_solar_path_sample_sets
                    .as_ref()
                    .and_then(|sets| sets.path_index_for_bearing(bearing_bits));
                buffers.shared_solar_path_sample_set_bearing_bits = bearing_bits;
            }
            buffers.shared_solar_path_bearing_matches = buffers
                .shared_solar_path_samples
                .as_ref()
                .is_some_and(|samples| samples.bearing_bits == bearing_bits)
                || buffers.shared_solar_path_sample_set_index.is_some();
        }
        let cached = if solar {
            buffers.solar_horizontal_path.get(index).copied()
        } else {
            buffers.observer_horizontal_path_node(index)
        };
        if let Some(node) = cached {
            return node;
        }
        if !solar {
            if buffers
                .observer_path_active_span
                .is_some_and(|span| index >= span.len)
            {
                buffers.materialize_observer_path_slab();
            }
        }
        if solar {
            let bearing_bits = bearing_deg.to_bits();
            let shared = buffers
                .shared_solar_path_nodes
                .as_ref()
                .filter(|path| path.bearing_bits == bearing_bits)
                .map(|path| path.as_ref())
                .or_else(|| {
                    buffers
                        .shared_solar_path_node_sets
                        .as_ref()
                        .and_then(|paths| {
                            buffers
                                .shared_solar_path_sample_set_index
                                .and_then(|path_index| paths.paths.get(path_index))
                                .filter(|path| path.bearing_bits == bearing_bits)
                                .or_else(|| {
                                    paths
                                        .paths
                                        .iter()
                                        .find(|path| path.bearing_bits == bearing_bits)
                                })
                        })
                });
            if let Some(shared) = shared {
                let origin_cell = if index == 0 {
                    let cell = cell_for_point(
                        origin_lat,
                        origin_lon,
                        self.lat0,
                        self.lon0,
                        self.spacing,
                        self.rows,
                        self.cols,
                    );
                    let exact_grid_origin = cell.filter(|cell| {
                        let row = *cell / self.cols;
                        let col = *cell % self.cols;
                        let expected_lat = self.lat0 + row as f32 * self.spacing;
                        let expected_lon = self.lon0 + col as f32 * self.spacing;
                        origin_lat.to_bits() == expected_lat.to_bits()
                            && origin_lon.to_bits() == expected_lon.to_bits()
                    });
                    buffers.shared_solar_path_cell = exact_grid_origin;
                    exact_grid_origin
                } else {
                    buffers.shared_solar_path_cell
                };
                if let Some(node) = origin_cell.and_then(|cell| shared.node(cell, index)) {
                    debug_assert_eq!(node.distance_km.to_bits(), distance_km.to_bits());
                    return node;
                }
            }
        }
        let distance_trig = buffers.cached_path_distance_trig(index, distance_km);
        let origin_cell = (index == 0 && distance_km == 0.0)
            .then(|| self.cached_path_origin(origin_lat, origin_lon))
            .flatten();
        let path_origin = origin_cell.map(|(_, origin)| origin);
        let path_geometry = if solar {
            if index == 0 || buffers.solar_path_geometry.is_none() {
                let (bearing_sin, bearing_cos) = buffers.cached_solar_bearing_trig(bearing_deg);
                buffers.solar_path_geometry = Some(match path_origin {
                    Some(origin) => DestinationPathGeometry::from_origin_with_bearing_trig(
                        origin,
                        bearing_sin,
                        bearing_cos,
                    ),
                    None => DestinationPathGeometry::new(origin_lat, origin_lon, bearing_deg),
                });
            }
            Some(buffers.solar_path_geometry.unwrap())
        } else {
            if index == 0 || buffers.observer_path_geometry.is_none() {
                buffers.observer_path_geometry = Some(match path_origin {
                    Some(origin) => {
                        let bearing_rad = bearing_deg.to_radians();
                        let (bearing_sin, bearing_cos) = bearing_rad.sin_cos();
                        DestinationPathGeometry::from_origin_with_bearing_trig(
                            origin,
                            bearing_sin,
                            bearing_cos,
                        )
                    }
                    None => DestinationPathGeometry::new(origin_lat, origin_lon, bearing_deg),
                });
            }
            Some(buffers.observer_path_geometry.unwrap())
        };
        let (lat, lon) = if distance_km == 0.0 {
            (origin_lat, origin_lon)
        } else {
            path_geometry
                .unwrap_or_else(|| {
                    DestinationPathGeometry::new(origin_lat, origin_lon, bearing_deg)
                })
                .target_point_with_trig(distance_trig.sin, distance_trig.cos)
        };
        let cell = origin_cell.map(|(cell, _)| Some(cell)).unwrap_or_else(|| {
            cell_for_point(
                lat,
                lon,
                self.lat0,
                self.lon0,
                self.spacing,
                self.rows,
                self.cols,
            )
        });
        let horizontal = self.horizontal_weights(lat, lon);
        let node = HorizontalPathNode {
            distance_km,
            sample_tangent: distance_trig.tangent,
            cell,
            horizontal,
        };
        if solar {
            debug_assert_eq!(buffers.solar_horizontal_path.len(), index);
            buffers.solar_horizontal_path.push(node);
        } else {
            debug_assert_eq!(buffers.observer_horizontal_path.len(), index);
            buffers.observer_horizontal_path.push(node);
            buffers.observer_midpoint_path.resize(
                buffers.observer_horizontal_path.len(),
                ObserverMidpointGeometry::default(),
            );
        }
        node
    }

    fn cached_observer_midpoint_geometry(
        &self,
        buffers: &mut SkyBuffers,
        index: usize,
        previous_distance_km: f32,
        distance_km: f32,
        observer_lat: f32,
        observer_lon: f32,
        target_bearing_deg: f32,
    ) -> ([(usize, f32); 4], f32) {
        let midpoint_distance_km = 0.5 * (previous_distance_km + distance_km);
        let midpoint_bits = midpoint_distance_km.to_bits();
        if let Some(cached) = buffers.observer_midpoint_geometry(index) {
            if cached.distance_bits == midpoint_bits {
                return (cached.horizontal, cached.tangent);
            }
        }
        let (midpoint_lat, midpoint_lon) = target_point(
            observer_lat,
            observer_lon,
            target_bearing_deg,
            midpoint_distance_km,
        );
        let midpoint_horizontal = self.horizontal_weights(midpoint_lat, midpoint_lon);
        let midpoint_tangent = (midpoint_distance_km / EARTH_RADIUS_KM).tan();
        buffers.store_observer_midpoint_geometry(
            index,
            ObserverMidpointGeometry {
                distance_bits: midpoint_bits,
                horizontal: midpoint_horizontal,
                tangent: midpoint_tangent,
            },
        );
        (midpoint_horizontal, midpoint_tangent)
    }

    fn cached_motion_cloud_layers_at_path_node(
        &self,
        buffers: &mut SkyBuffers,
        solar: bool,
        path_index: usize,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        horizontal: &[(usize, f32); 4],
    ) -> [CloudLayerSample; 3] {
        if solar {
            if let (Some(cache), Some(key_index), Some(origin_cell)) = (
                buffers.selected_shared_solar_path_samples(),
                buffers.shared_solar_path_sample_index,
                buffers.shared_solar_path_cell,
            ) {
                if buffers.shared_solar_path_bearing_matches {
                    if let Some(cached) = cache.cloud_layers(key_index, origin_cell, path_index) {
                        return cached;
                    }
                }
            }
        }
        let cached = if solar {
            buffers.solar_path_cloud_layers.get(path_index).copied()
        } else if let Some(span) = buffers.observer_path_active_span {
            let absolute_index = span.start.saturating_add(path_index);
            if buffers
                .observer_path_cloud_generation
                .get(absolute_index)
                .copied()
                == Some(buffers.layer_cache_current_generation)
            {
                buffers
                    .observer_path_cloud_slab
                    .get(absolute_index)
                    .copied()
            } else {
                None
            }
        } else {
            buffers.observer_path_cloud_layers.get(path_index).copied()
        };
        if let Some(cached) = cached {
            return cached;
        }

        let layers = self.cached_motion_cloud_layers_with_weights(
            buffers,
            left,
            right,
            weight,
            left_seconds,
            right_seconds,
            horizontal,
        );
        if solar {
            if buffers.solar_path_cloud_layers.len() <= path_index {
                buffers
                    .solar_path_cloud_layers
                    .resize(path_index, [CloudLayerSample::default(); 3]);
                buffers.solar_path_cloud_layers.push(layers);
            }
        } else if let Some(span) = buffers.observer_path_active_span {
            let absolute_index = span.start.saturating_add(path_index);
            if buffers.observer_path_cloud_slab.len() <= absolute_index {
                buffers
                    .observer_path_cloud_slab
                    .resize(absolute_index + 1, [CloudLayerSample::default(); 3]);
                buffers
                    .observer_path_cloud_generation
                    .resize(absolute_index + 1, 0);
            }
            buffers.observer_path_cloud_slab[absolute_index] = layers;
            buffers.observer_path_cloud_generation[absolute_index] =
                buffers.layer_cache_current_generation;
        } else {
            if buffers.observer_path_cloud_layers.len() <= path_index {
                buffers
                    .observer_path_cloud_layers
                    .resize(path_index, [CloudLayerSample::default(); 3]);
                buffers.observer_path_cloud_layers.push(layers);
            }
        }
        layers
    }

    fn temporal_aerosol_column(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        cell: usize,
    ) -> AerosolColumn {
        let aod550 = self.interpolated(left, right, weight, cell, AEROSOL_AOD550);
        if self.aerosol_profiles.is_empty() {
            return AerosolColumn {
                aod550,
                extinction_m_inv: [0.0; AEROSOL_PROFILE_LEVELS],
            };
        }
        let left_index =
            (left.min(self.time_count - 1) * self.cell_count + cell) * AEROSOL_PROFILE_LEVELS;
        let right_index =
            (right.min(self.time_count - 1) * self.cell_count + cell) * AEROSOL_PROFILE_LEVELS;
        let extinction_m_inv = std::array::from_fn(|level| {
            let left_value = self.aerosol_profiles[left_index + level];
            let right_value = self.aerosol_profiles[right_index + level];
            left_value + (right_value - left_value) * weight
        });
        AerosolColumn {
            aod550,
            extinction_m_inv,
        }
    }

    fn cached_aerosol_column(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        cell: usize,
    ) -> AerosolColumn {
        if cell < self.cell_count && cell < buffers.aerosol_column_cache.len() {
            if buffers.aerosol_column_cache_generation[cell]
                == buffers.layer_cache_current_generation
            {
                return buffers.aerosol_column_cache[cell];
            }
            let value = self.temporal_aerosol_column(left, right, weight, cell);
            buffers.aerosol_column_cache[cell] = value;
            buffers.aerosol_column_cache_generation[cell] = buffers.layer_cache_current_generation;
            return value;
        }
        self.temporal_aerosol_column(left, right, weight, cell)
    }

    fn interpolated_aerosol_column(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        horizontal: &[(usize, f32); 4],
    ) -> AerosolColumn {
        if buffers.aerosol_empty {
            return AerosolColumn {
                aod550: 0.0,
                extinction_m_inv: [0.0; AEROSOL_PROFILE_LEVELS],
            };
        }
        let mut aod550 = 0.0f32;
        let mut aod_total_weight = 0.0f32;
        let mut total_weight = 0.0f32;
        let mut extinction_sums = [0.0f32; AEROSOL_PROFILE_LEVELS];
        let has_profiles = !self.aerosol_profiles.is_empty();
        for index in 0..4 {
            let (cell, horizontal_weight) = horizontal[index];
            if !(horizontal_weight > 0.0) {
                continue;
            }
            let column = self.cached_aerosol_column(buffers, left, right, weight, cell);
            if column.aod550.is_finite() {
                aod550 += horizontal_weight * column.aod550;
                aod_total_weight += horizontal_weight;
            }
            total_weight += horizontal_weight;
            if has_profiles {
                for level in 0..AEROSOL_PROFILE_LEVELS {
                    extinction_sums[level] += horizontal_weight * column.extinction_m_inv[level];
                }
            }
        }
        let aod550 = if aod_total_weight > 0.0 {
            aod550 / aod_total_weight
        } else {
            0.0
        };
        if self.aerosol_profiles.is_empty() {
            return AerosolColumn {
                aod550,
                extinction_m_inv: [0.0; AEROSOL_PROFILE_LEVELS],
            };
        }
        let extinction_m_inv = extinction_sums.map(|value| {
            if total_weight > 0.0 {
                value / total_weight
            } else {
                f32::NAN
            }
        });
        AerosolColumn {
            aod550,
            extinction_m_inv,
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn cached_aerosol_path_cache(
        &self,
        buffers: &mut SkyBuffers,
        solar: bool,
        path_index: usize,
        left: usize,
        right: usize,
        weight: f32,
        horizontal: &[(usize, f32); 4],
        terrain_asl_m: f32,
    ) -> Option<AerosolPathCache> {
        if solar && buffers.shared_solar_path_aerosol_enabled {
            if let (Some(cache), Some(key_index), Some(origin_cell)) = (
                buffers.selected_shared_solar_path_samples(),
                buffers.shared_solar_path_sample_index,
                buffers.shared_solar_path_cell,
            ) {
                if buffers.shared_solar_path_bearing_matches {
                    if let Some(cached) = cache.aerosol_path(key_index, origin_cell, path_index) {
                        return Some(cached);
                    }
                }
            }
        }
        let cached = if solar {
            buffers.solar_aerosol_path_cache.get(path_index).copied()
        } else {
            buffers.observer_aerosol_path_cache.get(path_index).copied()
        };
        if let Some(cached) = cached {
            return Some(cached);
        }
        let column = self.interpolated_aerosol_column(buffers, left, right, weight, horizontal);
        let support = AerosolColumn::support(terrain_asl_m);
        let horizontal_weights_are_valid = horizontal.iter().all(|(_, value)| value.is_finite())
            && horizontal.iter().any(|(_, value)| *value > 0.0);
        let valid = if buffers.aerosol_interpolation_fast_path_safe
            && weight.is_finite()
            && horizontal_weights_are_valid
        {
            column.aod550.is_finite() && (0.0..=10.0).contains(&column.aod550)
        } else {
            column.is_valid()
        };
        let normalization = if valid && column.aod550 != 0.0 {
            column.normalization_integral(terrain_asl_m)
        } else {
            0.0
        };
        let cache = if solar {
            &mut buffers.solar_aerosol_path_cache
        } else {
            &mut buffers.observer_aerosol_path_cache
        };
        if cache.len() <= path_index {
            debug_assert_eq!(cache.len(), path_index);
            cache.push(AerosolPathCache {
                normalization,
                support,
                valid,
                column,
            });
        }
        Some(cache[path_index])
    }

    #[allow(clippy::too_many_arguments)]
    #[inline(always)]
    fn cached_aerosol_extinction_at_path_node(
        &self,
        buffers: &mut SkyBuffers,
        solar: bool,
        path_index: usize,
        left: usize,
        right: usize,
        weight: f32,
        horizontal: &[(usize, f32); 4],
        height_asl_m: f32,
        terrain_asl_m: f32,
    ) -> f32 {
        if buffers.aerosol_empty {
            return 0.0;
        }
        self.cached_aerosol_path_cache(
            buffers,
            solar,
            path_index,
            left,
            right,
            weight,
            horizontal,
            terrain_asl_m,
        )
        .map(|cached| {
            cached
                .column
                .extinction_km_inv_from_path_cache(height_asl_m, cached)
        })
        .unwrap_or(0.0)
    }

    #[allow(clippy::too_many_arguments)]
    #[inline(always)]
    fn cached_aerosol_extinction_at_path_node_layers(
        &self,
        buffers: &mut SkyBuffers,
        solar: bool,
        path_index: usize,
        left: usize,
        right: usize,
        weight: f32,
        horizontal: &[(usize, f32); 4],
        heights_asl_m: [f32; 3],
        live_layers: [bool; 3],
        terrain_asl_m: f32,
    ) -> [f32; 3] {
        if buffers.aerosol_empty {
            return [0.0; 3];
        }
        let Some(cached) = self.cached_aerosol_path_cache(
            buffers,
            solar,
            path_index,
            left,
            right,
            weight,
            horizontal,
            terrain_asl_m,
        ) else {
            return [0.0; 3];
        };
        if live_layers == [true; 3] {
            return heights_asl_m.map(|height| {
                cached
                    .column
                    .extinction_km_inv_from_path_cache(height, cached)
            });
        }
        std::array::from_fn(|layer| {
            if live_layers[layer] {
                cached
                    .column
                    .extinction_km_inv_from_path_cache(heights_asl_m[layer], cached)
            } else {
                0.0
            }
        })
    }

    fn horizontal_at_time(&self, time: usize, horizontal: &[(usize, f32); 4], field: usize) -> f32 {
        let time = time.min(self.time_count.saturating_sub(1));
        let mut value = 0.0f32;
        let mut total_weight = 0.0f32;
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 {
                continue;
            }
            let sample = self.value(time, cell, field);
            if sample.is_finite() {
                value += horizontal_weight * sample;
                total_weight += horizontal_weight;
            }
        }
        if total_weight > 0.0 {
            value / total_weight
        } else {
            0.0
        }
    }

    fn horizontal_amount_weighted_at_time(
        &self,
        time: usize,
        horizontal: &[(usize, f32); 4],
        amount_field: usize,
        field: usize,
    ) -> f32 {
        let time = time.min(self.time_count.saturating_sub(1));
        let mut value = 0.0f32;
        let mut total_weight = 0.0f32;
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 {
                continue;
            }
            let amount = self.value(time, cell, amount_field);
            let sample = self.value(time, cell, field);
            if amount.is_finite() && amount > 0.0 && sample.is_finite() {
                let member_weight = horizontal_weight * clamp01(amount);
                value += member_weight * sample;
                total_weight += member_weight;
            }
        }
        if total_weight > 0.0 {
            value / total_weight
        } else {
            0.0
        }
    }

    #[cfg(test)]
    fn family_wind_at_position(&self, time: usize, lat: f32, lon: f32, layer: usize) -> (f32, f32) {
        let (u_field, v_field) = match layer {
            0 => (HIGH_U, HIGH_V),
            1 => (MID_U, MID_V),
            _ => (LOW_U, LOW_V),
        };
        let Some(horizontal) = self.cloud_horizontal_weights(lat, lon) else {
            return (0.0, 0.0);
        };
        let u = self.horizontal_at_time(time, &horizontal, u_field);
        let v = self.horizontal_at_time(time, &horizontal, v_field);
        if u.is_finite() && v.is_finite() {
            (u, v)
        } else {
            (0.0, 0.0)
        }
    }

    #[cfg(test)]
    fn wind_displaced_position(
        &self,
        time: usize,
        lat: f32,
        lon: f32,
        layer: usize,
        seconds: f32,
        direction: f32,
    ) -> (f32, f32) {
        if seconds <= 0.0 || !seconds.is_finite() {
            return (lat, lon);
        }
        let (u_ms, v_ms) = self.family_wind_at_position(time, lat, lon, layer);
        self.wind_displaced_position_with_wind(lat, lon, seconds, direction, u_ms, v_ms)
    }

    fn wind_displaced_position_with_wind(
        &self,
        lat: f32,
        lon: f32,
        seconds: f32,
        direction: f32,
        u_ms: f32,
        v_ms: f32,
    ) -> (f32, f32) {
        if seconds <= 0.0 || !seconds.is_finite() {
            return (lat, lon);
        }
        let dlat_deg = (v_ms * seconds / (EARTH_RADIUS_KM * 1000.0)).to_degrees();
        let latitude_cosine = lat.to_radians().cos().abs().max(0.2);
        let dlon_deg = (u_ms * seconds / (EARTH_RADIUS_KM * 1000.0 * latitude_cosine)).to_degrees();
        let displaced_lat = (lat + direction * dlat_deg).clamp(-89.9, 89.9);
        let displaced_lon = (lon + direction * dlon_deg + 180.0).rem_euclid(360.0) - 180.0;
        let displaced_lon = longitude_on_grid(displaced_lon, self.lon0, self.spacing, self.cols);
        (displaced_lat, displaced_lon)
    }

    #[cfg(test)]
    fn cloud_layer_sample_at_time_position(
        &self,
        time: usize,
        lat: f32,
        lon: f32,
        layer: usize,
    ) -> CloudLayerSample {
        let Some(horizontal) = self.cloud_horizontal_weights(lat, lon) else {
            return CloudLayerSample::default();
        };
        self.cloud_layer_sample_at_time_with_horizontal(time, &horizontal, layer)
    }

    #[cfg(test)]
    fn cloud_layer_sample_at_time_with_horizontal(
        &self,
        time: usize,
        horizontal: &[(usize, f32); 4],
        layer: usize,
    ) -> CloudLayerSample {
        Self::cloud_layer_sample_at_time_with_horizontal_and_scalar::<false>(
            self, time, horizontal, layer, 0,
        )
        .0
    }

    fn cloud_layer_sample_and_scalar_at_time_with_horizontal(
        &self,
        time: usize,
        horizontal: &[(usize, f32); 4],
        layer: usize,
        scalar_field: usize,
    ) -> (CloudLayerSample, f32) {
        Self::cloud_layer_sample_at_time_with_horizontal_and_scalar::<true>(
            self,
            time,
            horizontal,
            layer,
            scalar_field,
        )
    }

    #[inline]
    fn cloud_layer_sample_at_time_with_horizontal_and_scalar<const INCLUDE_SCALAR: bool>(
        &self,
        time: usize,
        horizontal: &[(usize, f32); 4],
        layer: usize,
        scalar_field: usize,
    ) -> (CloudLayerSample, f32) {
        match layer {
            HIGH => self
                .cloud_layer_sample_at_time_with_horizontal_and_scalar_impl::<INCLUDE_SCALAR, HIGH>(
                    time,
                    horizontal,
                    layer,
                    scalar_field,
                ),
            MID => self
                .cloud_layer_sample_at_time_with_horizontal_and_scalar_impl::<INCLUDE_SCALAR, MID>(
                    time,
                    horizontal,
                    layer,
                    scalar_field,
                ),
            _ => self
                .cloud_layer_sample_at_time_with_horizontal_and_scalar_impl::<INCLUDE_SCALAR, LOW>(
                    time,
                    horizontal,
                    layer,
                    scalar_field,
                ),
        }
    }

    fn cloud_layer_sample_at_time_with_horizontal_and_scalar_impl<
        const INCLUDE_SCALAR: bool,
        const LAYER: usize,
    >(
        &self,
        time: usize,
        horizontal: &[(usize, f32); 4],
        layer: usize,
        scalar_field: usize,
    ) -> (CloudLayerSample, f32) {
        // The test-only LAYER=3 instantiation retains dynamic field selection
        // and the original independent per-field weight accumulators.
        let layer = if LAYER < 3 { LAYER } else { layer };
        let finite = |value: f32| if value.is_finite() { value } else { 0.0 };
        let (
            amount_field,
            opacity_field,
            material_field,
            base_field,
            top_field,
            pressure_low,
            pressure_high,
        ) = match layer {
            0 => (
                HIGH,
                HIGH_BLOCKER_OPACITY,
                HIGH_MATERIAL,
                HIGH_BASE,
                HIGH_TOP,
                0.0,
                HIGH_CLOUD_MAX_PRESSURE_HPA,
            ),
            1 => (
                MID,
                MID_BLOCKER_OPACITY,
                MID_MATERIAL,
                MID_BASE,
                MID_TOP,
                HIGH_CLOUD_MAX_PRESSURE_HPA,
                MID_CLOUD_MAX_PRESSURE_HPA,
            ),
            _ => (
                LOW,
                LOW_BLOCKER_OPACITY,
                LOW_MATERIAL,
                LOW_BASE,
                LOW_TOP,
                700.0,
                1100.0,
            ),
        };
        let mut amount_sum = 0.0f32;
        let mut amount_weight = 0.0f32;
        let mut amount_values = [0.0f32; 4];
        let scalar_time = time.min(self.time_count.saturating_sub(1));
        let mut scalar_sum = 0.0f32;
        let mut scalar_weight = 0.0f32;
        for (index, &(cell, horizontal_weight)) in horizontal.iter().enumerate() {
            if horizontal_weight <= 0.0 {
                continue;
            }
            let amount_value = self.value(time, cell, amount_field);
            amount_values[index] = amount_value;
            if amount_value.is_finite() {
                amount_sum += horizontal_weight * amount_value;
                amount_weight += horizontal_weight;
            }
            if INCLUDE_SCALAR {
                let sample = self.value(scalar_time, cell, scalar_field);
                if sample.is_finite() {
                    scalar_sum += horizontal_weight * sample;
                    scalar_weight += horizontal_weight;
                }
            }
        }
        let scalar = if scalar_weight > 0.0 {
            scalar_sum / scalar_weight
        } else {
            0.0
        };
        let amount = clamp01(finite(if amount_weight > 0.0 {
            amount_sum / amount_weight
        } else {
            0.0
        }));
        if amount <= 0.0 {
            return (CloudLayerSample::default(), scalar);
        }
        let fields = [
            base_field,
            top_field,
            PRESSURE,
            opacity_field,
            material_field,
        ];
        let mut weighted_sums = [0.0f32; 5];
        let mut weighted_totals = [0.0f32; 5];
        let mut common_total = 0.0f32;
        let mut common_total_only = LAYER < 3;
        for (index, &(cell, horizontal_weight)) in horizontal.iter().enumerate() {
            if horizontal_weight <= 0.0 {
                continue;
            }
            let amount_value = amount_values[index];
            if !amount_value.is_finite() || amount_value <= 0.0 {
                continue;
            }
            let member_weight = horizontal_weight * clamp01(amount_value);
            let values = fields.map(|field| self.value(time, cell, field));
            if common_total_only && values.iter().all(|value| value.is_finite()) {
                for field_index in 0..5 {
                    weighted_sums[field_index] += member_weight * values[field_index];
                }
                common_total += member_weight;
            } else {
                // Earlier all-finite vertices gave every field this exact
                // accumulator. Preserve per-field exclusion after a miss.
                if common_total_only {
                    weighted_totals.fill(common_total);
                    common_total_only = false;
                }
                for (field_index, sample) in values.into_iter().enumerate() {
                    if sample.is_finite() {
                        weighted_sums[field_index] += member_weight * sample;
                        weighted_totals[field_index] += member_weight;
                    }
                }
            }
        }
        let weighted = |field_index: usize| {
            let total = if common_total_only {
                common_total
            } else {
                weighted_totals[field_index]
            };
            if total > 0.0 {
                weighted_sums[field_index] / total
            } else {
                0.0
            }
        };
        let mut base_m = finite(weighted(0));
        let mut top_m = finite(weighted(1));
        let selected_pressure = finite(weighted(2));
        if top_m <= base_m && selected_pressure > pressure_low && selected_pressure <= pressure_high
        {
            base_m = finite(self.horizontal_amount_weighted_at_time(
                time,
                &horizontal,
                amount_field,
                BASE,
            ));
            top_m = finite(self.horizontal_amount_weighted_at_time(
                time,
                &horizontal,
                amount_field,
                TOP,
            ));
        }
        if top_m <= base_m {
            top_m = base_m
                + match layer {
                    0 => 1200.0,
                    1 => 900.0,
                    _ => 600.0,
                };
        }
        (
            CloudLayerSample {
                amount,
                opacity: clamp01(finite(weighted(3))),
                material_quality: clamp01(finite(weighted(4))),
                base_m,
                top_m,
            },
            scalar,
        )
    }

    #[cfg(test)]
    fn semi_lagrangian_cloud_layer_sample(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        lat: f32,
        lon: f32,
        layer: usize,
    ) -> CloudLayerSample {
        let (left_lat, left_lon) =
            self.wind_displaced_position(left, lat, lon, layer, left_seconds, -1.0);
        let (right_lat, right_lon) =
            self.wind_displaced_position(right, lat, lon, layer, right_seconds, 1.0);
        // Match the production motion sampler: displaced samples clamp to the
        // grid edge instead of reporting clear sky (one-cell extrapolation).
        let left_sample = self.cloud_layer_sample_at_time_position(
            left,
            self.clamp_to_grid_lat(left_lat),
            self.clamp_to_grid_lon(left_lon),
            layer,
        );
        let right_sample = self.cloud_layer_sample_at_time_position(
            right,
            self.clamp_to_grid_lat(right_lat),
            self.clamp_to_grid_lon(right_lon),
            layer,
        );
        let dither = Self::blend_dither_threshold(
            left_lat * 13.0 + left_lon * 7.0 + right_lat * 17.0 + right_lon * 5.0,
        );
        Self::blend_cloud_layer_samples(left_sample, right_sample, weight, dither)
    }

    /// Deterministic dither in [0, 1) from a spatial/temporal seed: across
    /// cells the thresholds are uniform, so the ensemble mean matches the
    /// linear blend while every single cell keeps a binary 0/full deck.
    fn blend_dither_threshold(seed: f32) -> f32 {
        let s = (seed * 127.1 + 311.7).sin() * 43758.5453;
        s - s.floor()
    }

    fn blend_cloud_layer_samples(
        left_sample: CloudLayerSample,
        right_sample: CloudLayerSample,
        weight: f32,
        dither: f32,
    ) -> CloudLayerSample {
        let weight = clamp01(weight);
        // Duty-cycle crossing: when exactly one bracket side holds a deck,
        // linear blending dilutes it into a permanent half-wall and erases
        // the burn duration (exp(-0.5*tau) != 0.5*exp(-tau) + 0.5). Select
        // one side by the deterministic dither instead: unbiased across
        // cells, and the chosen step keeps the full deck properties.
        const PRESENCE_FLOOR: f32 = 0.02;
        let left_present = left_sample.amount >= PRESENCE_FLOOR;
        let right_present = right_sample.amount >= PRESENCE_FLOOR;
        if left_present != right_present {
            let take_right = weight >= clamp01(dither);
            let chosen = if take_right {
                right_sample
            } else {
                left_sample
            };
            if chosen.amount <= 0.0 {
                return CloudLayerSample::default();
            }
            return chosen;
        }
        let amount =
            clamp01(left_sample.amount + (right_sample.amount - left_sample.amount) * weight);
        if amount <= 0.0 {
            return CloudLayerSample::default();
        }
        let left_amount_weight = (1.0 - weight) * left_sample.amount;
        let right_amount_weight = weight * right_sample.amount;
        let attribute = |left_value: f32, right_value: f32| {
            (left_amount_weight * left_value + right_amount_weight * right_value)
                / (left_amount_weight + right_amount_weight).max(1e-6)
        };
        CloudLayerSample {
            amount,
            opacity: clamp01(attribute(left_sample.opacity, right_sample.opacity)),
            material_quality: clamp01(attribute(
                left_sample.material_quality,
                right_sample.material_quality,
            )),
            // Cloud boundaries are ASL geometry. Preserve signed values for
            // below-sea-level terrain; AGL lower bounds belong only in the
            // aerosol and observer-relative calculations.
            base_m: attribute(left_sample.base_m, right_sample.base_m),
            top_m: attribute(left_sample.top_m, right_sample.top_m),
        }
    }

    fn motion_positions_at_position(
        &self,
        lat: f32,
        lon: f32,
        left_seconds: f32,
        right_seconds: f32,
        winds: [f32; 4],
        midpoint_wind: impl Fn(f32, f32, usize) -> (f32, f32),
    ) -> [f32; 4] {
        // RK2 midpoint recovers the leading shear correction to a constant
        // source-wind trajectory. For along-track shear a, the local error
        // scale is |a|*t^2/6: a=1e-5 s^-1 over three hours is about 0.65 km.
        // This is still one explicit midpoint step, not an exact trajectory;
        // validate finer stepping against real shear before changing it.
        let displaced_side = |seconds: f32, direction: f32, source: (f32, f32), side: usize| {
            let (half_lat, half_lon) = self.wind_displaced_position_with_wind(
                lat,
                lon,
                seconds * 0.5,
                direction,
                source.0,
                source.1,
            );
            let (mid_u, mid_v) = midpoint_wind(half_lat, half_lon, side);
            self.wind_displaced_position_with_wind(lat, lon, seconds, direction, mid_u, mid_v)
        };
        let (left_lat, left_lon) = displaced_side(left_seconds, -1.0, (winds[0], winds[1]), 0);
        let (right_lat, right_lon) = displaced_side(right_seconds, 1.0, (winds[2], winds[3]), 1);
        // Wind displacement can push the upstream sample outside the prepared
        // grid, especially past f120 where the model bracket spans 3-6 hours.
        // Clamping to the nearest grid edge keeps a real boundary deck visible
        // (one-cell edge extrapolation) instead of silently reporting clear
        // sky, which used to understate blocking near the domain edge.
        [
            self.clamp_to_grid_lat(left_lat),
            self.clamp_to_grid_lon(left_lon),
            self.clamp_to_grid_lat(right_lat),
            self.clamp_to_grid_lon(right_lon),
        ]
    }

    fn clamp_to_grid_lat(&self, lat: f32) -> f32 {
        if self.rows == 0 {
            return lat;
        }
        let max_lat = self.lat0 + self.spacing * (self.rows - 1) as f32;
        lat.clamp(self.lat0, max_lat)
    }

    fn clamp_to_grid_lon(&self, lon: f32) -> f32 {
        if self.cols == 0 {
            return lon;
        }
        let lon = longitude_on_grid(lon, self.lon0, self.spacing, self.cols);
        let max_lon = self.lon0 + self.spacing * (self.cols - 1) as f32;
        lon.clamp(self.lon0, max_lon)
    }

    fn motion_cloud_layer_sample_at_positions_with_scalars(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        layer: usize,
        positions: [f32; 4],
    ) -> (CloudLayerSample, f32) {
        let body_field = match layer {
            HIGH => HIGH_TARGET_BODY_RISK,
            MID => MID_TARGET_BODY_RISK,
            _ => LOW_TARGET_BODY_RISK,
        };
        let left_horizontal = self.cloud_horizontal_weights(positions[0], positions[1]);
        let right_horizontal = self.cloud_horizontal_weights(positions[2], positions[3]);
        let (left_sample, left_scalar) = left_horizontal
            .as_ref()
            .map(|horizontal| {
                self.cloud_layer_sample_and_scalar_at_time_with_horizontal(
                    left, horizontal, layer, body_field,
                )
            })
            .unwrap_or((CloudLayerSample::default(), 0.0));
        let (right_sample, right_scalar) = right_horizontal
            .as_ref()
            .map(|horizontal| {
                self.cloud_layer_sample_and_scalar_at_time_with_horizontal(
                    right, horizontal, layer, body_field,
                )
            })
            .unwrap_or((CloudLayerSample::default(), 0.0));
        let scalar_weight = clamp01(weight);
        let dither = Self::blend_dither_threshold(
            positions[0] * 13.0 + positions[1] * 7.0 + positions[2] * 17.0 + positions[3] * 5.0,
        );
        (
            Self::blend_cloud_layer_samples(left_sample, right_sample, weight, dither),
            left_scalar + (right_scalar - left_scalar) * scalar_weight,
        )
    }

    /// Sample one exact model cell without allocating full-grid worker caches.
    /// This is the shared-motion table builder's cold path; the cached observer
    /// path below uses the same wind/position/scalar helpers.
    fn motion_cell_samples(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        cell: usize,
    ) -> ([CloudLayerSample; 3], [f32; 6]) {
        if cell >= self.cell_count || self.cols == 0 {
            return ([CloudLayerSample::default(); 3], [0.0; 6]);
        }
        let row = cell / self.cols;
        let col = cell % self.cols;
        let lat = self.lat0 + row as f32 * self.spacing;
        let lon = self.lon0 + col as f32 * self.spacing;
        let horizontal = self.cloud_horizontal_weights(lat, lon);
        let wind_horizontal = if left_seconds > 0.0 && left_seconds.is_finite()
            || right_seconds > 0.0 && right_seconds.is_finite()
        {
            horizontal.as_ref()
        } else {
            None
        };
        let winds: [[f32; 4]; 3] = std::array::from_fn(|layer| {
            self.motion_wind_for_layer(
                wind_horizontal,
                left,
                right,
                left_seconds,
                right_seconds,
                layer,
            )
        });
        let mut layers = [CloudLayerSample::default(); 3];
        let mut scalars = [0.0f32; 6];
        for layer in 0..3 {
            let winds = winds[layer];
            let active = winds[0] != 0.0 || winds[1] != 0.0 || winds[2] != 0.0 || winds[3] != 0.0;
            let body_field = match layer {
                HIGH => HIGH_TARGET_BODY_RISK,
                MID => MID_TARGET_BODY_RISK,
                _ => LOW_TARGET_BODY_RISK,
            };
            if !active {
                layers[layer] = self.cloud_layer_sample(left, right, weight, cell, layer);
                if let Some(horizontal) = horizontal.as_ref() {
                    scalars[layer * 2] = self
                        .interpolated_with_horizontal(left, right, weight, horizontal, body_field);
                    scalars[layer * 2 + 1] = self.interpolated_with_horizontal(
                        left,
                        right,
                        weight,
                        horizontal,
                        LOCAL_UPPER_SUPPORT,
                    );
                }
                continue;
            }
            let positions = self.motion_positions_at_cell(
                left,
                right,
                lat,
                lon,
                left_seconds,
                right_seconds,
                layer,
                winds,
            );
            let (cloud, body_risk) = self.motion_cloud_layer_sample_at_positions_with_scalars(
                left, right, weight, layer, positions,
            );
            layers[layer] = cloud;
            if horizontal.is_some() {
                scalars[layer * 2] = body_risk;
                scalars[layer * 2 + 1] = self.motion_family_scalar_at_positions(
                    left,
                    right,
                    weight,
                    LOCAL_UPPER_SUPPORT,
                    positions,
                );
            }
        }
        (layers, scalars)
    }

    fn motion_wind_for_layer(
        &self,
        horizontal: Option<&[(usize, f32); 4]>,
        left: usize,
        right: usize,
        left_seconds: f32,
        right_seconds: f32,
        layer: usize,
    ) -> [f32; 4] {
        let (u_field, v_field) = match layer {
            0 => (HIGH_U, HIGH_V),
            1 => (MID_U, MID_V),
            _ => (LOW_U, LOW_V),
        };
        let wind_at = |time: usize| {
            let Some(horizontal) = horizontal else {
                return (0.0, 0.0);
            };
            let u = self.horizontal_at_time(time, horizontal, u_field);
            let v = self.horizontal_at_time(time, horizontal, v_field);
            if u.is_finite() && v.is_finite() {
                (u, v)
            } else {
                (0.0, 0.0)
            }
        };
        let (left_u, left_v) = if left_seconds > 0.0 && left_seconds.is_finite() {
            wind_at(left)
        } else {
            (0.0, 0.0)
        };
        let (right_u, right_v) = if right_seconds > 0.0 && right_seconds.is_finite() {
            wind_at(right)
        } else {
            (0.0, 0.0)
        };
        [left_u, left_v, right_u, right_v]
    }

    fn cached_motion_winds_for_cell(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        left_seconds: f32,
        right_seconds: f32,
        cell: usize,
        layer: usize,
    ) -> [f32; 4] {
        if cell >= self.cell_count || layer >= 3 || self.cols == 0 {
            return [0.0; 4];
        }
        let cache_index = cell * 3 + layer;
        if buffers.motion_state_cache_generation[cache_index]
            == buffers.layer_cache_current_generation
        {
            return buffers.motion_wind_cache[cache_index];
        }
        self.cache_motion_winds_for_cell_all_layers(
            buffers,
            left,
            right,
            left_seconds,
            right_seconds,
            cell,
        );
        buffers.motion_wind_cache[cache_index]
    }

    fn cached_motion_positions_for_cell(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        left_seconds: f32,
        right_seconds: f32,
        cell: usize,
        layer: usize,
        winds: [f32; 4],
    ) -> [f32; 4] {
        let cache_index = cell * 3 + layer;
        if buffers.motion_position_cache_generation[cache_index]
            == buffers.layer_cache_current_generation
        {
            return buffers.motion_position_cache[cache_index];
        }
        let row = cell / self.cols;
        let col = cell % self.cols;
        let lat = self.lat0 + row as f32 * self.spacing;
        let lon = self.lon0 + col as f32 * self.spacing;
        let positions = self.motion_positions_at_cell(
            left,
            right,
            lat,
            lon,
            left_seconds,
            right_seconds,
            layer,
            winds,
        );
        buffers.motion_position_cache[cache_index] = positions;
        buffers.motion_position_cache_generation[cache_index] =
            buffers.layer_cache_current_generation;
        positions
    }

    fn motion_positions_at_cell(
        &self,
        left: usize,
        right: usize,
        lat: f32,
        lon: f32,
        left_seconds: f32,
        right_seconds: f32,
        layer: usize,
        winds: [f32; 4],
    ) -> [f32; 4] {
        let (u_field, v_field) = match layer {
            0 => (HIGH_U, HIGH_V),
            1 => (MID_U, MID_V),
            _ => (LOW_U, LOW_V),
        };
        let midpoint_wind = |mid_lat: f32, mid_lon: f32, side: usize| -> (f32, f32) {
            let fallback = (winds[side * 2], winds[side * 2 + 1]);
            let Some(horizontal) = self.cloud_horizontal_weights(
                self.clamp_to_grid_lat(mid_lat),
                self.clamp_to_grid_lon(mid_lon),
            ) else {
                return fallback;
            };
            let time = if side == 0 { left } else { right };
            let u = self.horizontal_at_time(time, &horizontal, u_field);
            let v = self.horizontal_at_time(time, &horizontal, v_field);
            if u.is_finite() && v.is_finite() {
                (u, v)
            } else {
                fallback
            }
        };
        self.motion_positions_at_position(
            lat,
            lon,
            left_seconds,
            right_seconds,
            winds,
            midpoint_wind,
        )
    }

    fn cache_motion_winds_for_cell_all_layers(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        left_seconds: f32,
        right_seconds: f32,
        cell: usize,
    ) {
        if cell >= self.cell_count || self.cols == 0 {
            return;
        }
        let generation = buffers.layer_cache_current_generation;
        if buffers.motion_wind_all_generation[cell] == generation {
            return;
        }
        let missing = (0..3).any(|layer| {
            let cache_index = cell * 3 + layer;
            buffers.motion_state_cache_generation[cache_index] != generation
        });
        if !missing {
            buffers.motion_wind_all_generation[cell] = generation;
            return;
        }
        if let Some(winds) = buffers.shared_winds.as_ref().and_then(|cache| {
            buffers
                .shared_wind_index
                .and_then(|index| cache.lookup_index(index, cell))
        }) {
            for (layer, winds) in winds.into_iter().enumerate() {
                let cache_index = cell * 3 + layer;
                buffers.motion_wind_cache[cache_index] = winds;
                buffers.motion_state_cache[cache_index] = u8::from(
                    winds[0] != 0.0 || winds[1] != 0.0 || winds[2] != 0.0 || winds[3] != 0.0,
                );
                buffers.motion_state_cache_generation[cache_index] = generation;
            }
            buffers.motion_wind_all_generation[cell] = generation;
            return;
        }
        let row = cell / self.cols;
        let col = cell % self.cols;
        let lat = self.lat0 + row as f32 * self.spacing;
        let lon = self.lon0 + col as f32 * self.spacing;
        let horizontal = if left_seconds > 0.0 && left_seconds.is_finite()
            || right_seconds > 0.0 && right_seconds.is_finite()
        {
            self.cloud_horizontal_weights(lat, lon)
        } else {
            None
        };
        for layer in 0..3 {
            let cache_index = cell * 3 + layer;
            if buffers.motion_state_cache_generation[cache_index] == generation {
                continue;
            }
            let winds = self.motion_wind_for_layer(
                horizontal.as_ref(),
                left,
                right,
                left_seconds,
                right_seconds,
                layer,
            );
            buffers.motion_wind_cache[cache_index] = winds;
            buffers.motion_state_cache[cache_index] =
                u8::from(winds[0] != 0.0 || winds[1] != 0.0 || winds[2] != 0.0 || winds[3] != 0.0);
            buffers.motion_state_cache_generation[cache_index] = generation;
        }
        buffers.motion_wind_all_generation[cell] = generation;
    }

    fn cached_motion_state_for_cell(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        left_seconds: f32,
        right_seconds: f32,
        cell: usize,
        layer: usize,
    ) -> bool {
        if cell >= self.cell_count || layer >= 3 || self.cols == 0 {
            return false;
        }
        let cache_index = cell * 3 + layer;
        if buffers.motion_state_cache_generation[cache_index]
            == buffers.layer_cache_current_generation
        {
            return buffers.motion_state_cache[cache_index] != 0;
        }
        let winds = self.cached_motion_winds_for_cell(
            buffers,
            left,
            right,
            left_seconds,
            right_seconds,
            cell,
            layer,
        );
        let active = winds[0] != 0.0 || winds[1] != 0.0 || winds[2] != 0.0 || winds[3] != 0.0;
        buffers.motion_state_cache[cache_index] = u8::from(active);
        buffers.motion_state_cache_generation[cache_index] = buffers.layer_cache_current_generation;
        active
    }

    fn cached_motion_is_active_with_weights(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        left_seconds: f32,
        right_seconds: f32,
        horizontal: &[(usize, f32); 4],
        layer: usize,
    ) -> bool {
        horizontal.iter().any(|&(cell, horizontal_weight)| {
            horizontal_weight > 0.0
                && self.cached_motion_state_for_cell(
                    buffers,
                    left,
                    right,
                    left_seconds,
                    right_seconds,
                    cell,
                    layer,
                )
        })
    }

    fn cached_motion_cloud_layer_sample(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        lat: f32,
        lon: f32,
        layer: usize,
    ) -> CloudLayerSample {
        let Some(horizontal) = self.cloud_horizontal_weights(lat, lon) else {
            return CloudLayerSample::default();
        };
        self.cached_motion_cloud_layer_sample_with_weights(
            buffers,
            left,
            right,
            weight,
            left_seconds,
            right_seconds,
            &horizontal,
            layer,
        )
    }

    fn shared_motion_cloud_layers_with_weights(
        &self,
        buffers: &SkyBuffers,
        horizontal: &[(usize, f32); 4],
    ) -> Option<[CloudLayerSample; 3]> {
        let cache = buffers.shared_motion.as_ref()?;
        let key_index = buffers.shared_motion_index?;
        cache.layers_for_horizontal(key_index, self.cell_count, horizontal)
    }

    fn cached_motion_cloud_layers_with_weights(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        horizontal: &[(usize, f32); 4],
    ) -> [CloudLayerSample; 3] {
        if let Some(cached) = self.shared_motion_cloud_layers_with_weights(buffers, horizontal) {
            return cached;
        }
        // Finite temporal weights produce finite, nonnegative layer amounts.
        // With finite corner weights, omitted zero terms do not change the
        // original sum. Nonfinite inputs retain the two independent totals.
        if weight.is_finite() && horizontal.iter().all(|(_, value)| value.is_finite()) {
            self.cached_motion_cloud_layers_with_weights_impl::<true>(
                buffers,
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                horizontal,
            )
        } else {
            self.cached_motion_cloud_layers_with_weights_impl::<false>(
                buffers,
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                horizontal,
            )
        }
    }

    fn cached_motion_cloud_layers_with_weights_impl<const COMMON_AMOUNT: bool>(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        horizontal: &[(usize, f32); 4],
    ) -> [CloudLayerSample; 3] {
        let mut active = [false; 3];
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 || cell >= self.cell_count {
                continue;
            }
            self.cache_motion_winds_for_cell_all_layers(
                buffers,
                left,
                right,
                left_seconds,
                right_seconds,
                cell,
            );
            let cache_base = cell * 3;
            for layer in 0..3 {
                active[layer] |= buffers.motion_state_cache[cache_base + layer] != 0;
            }
        }
        let mut amount = [0.0f32; 3];
        let mut material_weight = [0.0f32; 3];
        let mut material = [[0.0f32; 4]; 3];
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 || cell >= self.cell_count {
                continue;
            }
            for layer in 0..3 {
                let cloud = if active[layer] {
                    let cache_index = cell * 3 + layer;
                    if buffers.motion_layer_cache_generation[cache_index]
                        == buffers.layer_cache_current_generation
                    {
                        buffers.motion_layer_cache[cache_index]
                    } else {
                        let positions = self.cached_motion_positions_for_cell(
                            buffers,
                            left,
                            right,
                            left_seconds,
                            right_seconds,
                            cell,
                            layer,
                            buffers.motion_wind_cache[cache_index],
                        );
                        let (value, body_risk) = self
                            .motion_cloud_layer_sample_at_positions_with_scalars(
                                left, right, weight, layer, positions,
                            );
                        buffers.motion_layer_cache[cache_index] = value;
                        buffers.motion_layer_cache_generation[cache_index] =
                            buffers.layer_cache_current_generation;
                        let scalar_base = cache_index * 2;
                        buffers.motion_scalar_cache[scalar_base] = body_risk;
                        buffers.motion_scalar_cache_generation[scalar_base] =
                            buffers.layer_cache_current_generation;
                        value
                    }
                } else {
                    self.cached_cloud_layer_sample(buffers, left, right, weight, cell, layer)
                };
                if !COMMON_AMOUNT {
                    amount[layer] += horizontal_weight * cloud.amount;
                }
                if cloud.amount > 0.0 {
                    let cloud_weight = horizontal_weight * cloud.amount;
                    material_weight[layer] += cloud_weight;
                    // Retain each field's corner order while placing its
                    // independent material sums in adjacent lanes.
                    let values = [
                        cloud.opacity,
                        cloud.material_quality,
                        cloud.base_m,
                        cloud.top_m,
                    ];
                    for field in 0..4 {
                        material[layer][field] += cloud_weight * values[field];
                    }
                }
            }
        }
        let result = std::array::from_fn(|layer| {
            if material_weight[layer] <= 1e-6 {
                return CloudLayerSample::default();
            }
            CloudLayerSample {
                amount: clamp01(if COMMON_AMOUNT {
                    material_weight[layer]
                } else {
                    amount[layer]
                }),
                opacity: clamp01(material[layer][0] / material_weight[layer]),
                material_quality: clamp01(material[layer][1] / material_weight[layer]),
                base_m: material[layer][2] / material_weight[layer],
                top_m: material[layer][3] / material_weight[layer],
            }
        });
        if result.iter().all(|cloud: &CloudLayerSample| {
            [
                cloud.amount,
                cloud.opacity,
                cloud.material_quality,
                cloud.base_m,
                cloud.top_m,
            ]
            .iter()
            .all(|value| value.is_finite())
        }) {
            result
        } else {
            // NaN payload propagation depends on the original scalar shape.
            // Reuse its complete body on the same cache key, never sanitize it.
            self.cached_motion_cloud_layers_original_scalar_impl::<COMMON_AMOUNT>(
                buffers,
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                horizontal,
            )
        }
    }

    #[cold]
    fn cached_motion_cloud_layers_original_scalar_impl<const COMMON_AMOUNT: bool>(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        horizontal: &[(usize, f32); 4],
    ) -> [CloudLayerSample; 3] {
        let mut active = [false; 3];
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 || cell >= self.cell_count {
                continue;
            }
            // One horizontal source cell supplies all three wind families.
            // Populate that cache once, then read the three flags directly;
            // the previous helper repeated the generation check per layer.
            self.cache_motion_winds_for_cell_all_layers(
                buffers,
                left,
                right,
                left_seconds,
                right_seconds,
                cell,
            );
            let cache_base = cell * 3;
            for layer in 0..3 {
                active[layer] |= buffers.motion_state_cache[cache_base + layer] != 0;
            }
        }

        let mut amount = [0.0f32; 3];
        let mut material_weight = [0.0f32; 3];
        let mut opacity = [0.0f32; 3];
        let mut material_quality = [0.0f32; 3];
        let mut base_m = [0.0f32; 3];
        let mut top_m = [0.0f32; 3];
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 || cell >= self.cell_count {
                continue;
            }
            for layer in 0..3 {
                let cloud = if active[layer] {
                    let cache_index = cell * 3 + layer;
                    if buffers.motion_layer_cache_generation[cache_index]
                        == buffers.layer_cache_current_generation
                    {
                        buffers.motion_layer_cache[cache_index]
                    } else {
                        let positions = self.cached_motion_positions_for_cell(
                            buffers,
                            left,
                            right,
                            left_seconds,
                            right_seconds,
                            cell,
                            layer,
                            buffers.motion_wind_cache[cache_index],
                        );
                        let (value, body_risk) = self
                            .motion_cloud_layer_sample_at_positions_with_scalars(
                                left, right, weight, layer, positions,
                            );
                        buffers.motion_layer_cache[cache_index] = value;
                        buffers.motion_layer_cache_generation[cache_index] =
                            buffers.layer_cache_current_generation;
                        let scalar_base = cache_index * 2;
                        buffers.motion_scalar_cache[scalar_base] = body_risk;
                        buffers.motion_scalar_cache_generation[scalar_base] =
                            buffers.layer_cache_current_generation;
                        value
                    }
                } else {
                    self.cached_cloud_layer_sample(buffers, left, right, weight, cell, layer)
                };
                if !COMMON_AMOUNT {
                    amount[layer] += horizontal_weight * cloud.amount;
                }
                if cloud.amount > 0.0 {
                    material_weight[layer] += horizontal_weight * cloud.amount;
                    opacity[layer] += horizontal_weight * cloud.amount * cloud.opacity;
                    material_quality[layer] +=
                        horizontal_weight * cloud.amount * cloud.material_quality;
                    base_m[layer] += horizontal_weight * cloud.amount * cloud.base_m;
                    top_m[layer] += horizontal_weight * cloud.amount * cloud.top_m;
                }
            }
        }

        std::array::from_fn(|layer| {
            if material_weight[layer] <= 1e-6 {
                return CloudLayerSample::default();
            }
            CloudLayerSample {
                amount: clamp01(if COMMON_AMOUNT {
                    material_weight[layer]
                } else {
                    amount[layer]
                }),
                opacity: clamp01(opacity[layer] / material_weight[layer]),
                material_quality: clamp01(material_quality[layer] / material_weight[layer]),
                base_m: base_m[layer] / material_weight[layer],
                top_m: top_m[layer] / material_weight[layer],
            }
        })
    }

    fn cached_motion_cloud_layer_sample_with_weights(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        horizontal: &[(usize, f32); 4],
        layer: usize,
    ) -> CloudLayerSample {
        if let Some(cached) = self.shared_motion_cloud_layers_with_weights(buffers, horizontal) {
            return cached.get(layer).copied().unwrap_or_default();
        }
        let mut active = false;
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 || cell >= self.cell_count {
                continue;
            }
            self.cache_motion_winds_for_cell_all_layers(
                buffers,
                left,
                right,
                left_seconds,
                right_seconds,
                cell,
            );
            active |= buffers.motion_state_cache[cell * 3 + layer] != 0;
        }
        if !active {
            return self.cached_horizontal_cloud_layer_sample_with_weights(
                buffers, left, right, weight, horizontal, layer,
            );
        }
        let mut amount = 0.0f32;
        let mut material_weight = 0.0f32;
        let mut opacity = 0.0f32;
        let mut material_quality = 0.0f32;
        let mut base_m = 0.0f32;
        let mut top_m = 0.0f32;
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 || cell >= self.cell_count || layer >= 3 {
                continue;
            }
            let cache_index = cell * 3 + layer;
            let cloud = if buffers.motion_layer_cache_generation[cache_index]
                == buffers.layer_cache_current_generation
            {
                buffers.motion_layer_cache[cache_index]
            } else {
                let winds = self.cached_motion_winds_for_cell(
                    buffers,
                    left,
                    right,
                    left_seconds,
                    right_seconds,
                    cell,
                    layer,
                );
                let positions = self.cached_motion_positions_for_cell(
                    buffers,
                    left,
                    right,
                    left_seconds,
                    right_seconds,
                    cell,
                    layer,
                    winds,
                );
                let (value, body_risk) = self.motion_cloud_layer_sample_at_positions_with_scalars(
                    left, right, weight, layer, positions,
                );
                buffers.motion_layer_cache[cache_index] = value;
                buffers.motion_layer_cache_generation[cache_index] =
                    buffers.layer_cache_current_generation;
                let scalar_base = cache_index * 2;
                buffers.motion_scalar_cache[scalar_base] = body_risk;
                buffers.motion_scalar_cache_generation[scalar_base] =
                    buffers.layer_cache_current_generation;
                value
            };
            amount += horizontal_weight * cloud.amount;
            if cloud.amount > 0.0 {
                material_weight += horizontal_weight * cloud.amount;
                opacity += horizontal_weight * cloud.amount * cloud.opacity;
                material_quality += horizontal_weight * cloud.amount * cloud.material_quality;
                base_m += horizontal_weight * cloud.amount * cloud.base_m;
                top_m += horizontal_weight * cloud.amount * cloud.top_m;
            }
        }
        if material_weight <= 1e-6 {
            return CloudLayerSample::default();
        }
        CloudLayerSample {
            amount: clamp01(amount),
            opacity: clamp01(opacity / material_weight),
            material_quality: clamp01(material_quality / material_weight),
            base_m: base_m / material_weight,
            top_m: top_m / material_weight,
        }
    }

    fn cached_motion_cloud_layer_sample_for_cell(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        cell: usize,
        layer: usize,
    ) -> CloudLayerSample {
        if cell >= self.cell_count || layer >= 3 {
            return CloudLayerSample::default();
        }
        if let (Some(cache), Some(key_index)) =
            (buffers.shared_motion.as_ref(), buffers.shared_motion_index)
        {
            if let Some(value) = cache.layer_for_cell(key_index, cell, layer) {
                return value;
            }
        }
        self.cache_motion_winds_for_cell_all_layers(
            buffers,
            left,
            right,
            left_seconds,
            right_seconds,
            cell,
        );
        if buffers.motion_state_cache[cell * 3 + layer] == 0 {
            return self.cached_cloud_layer_sample(buffers, left, right, weight, cell, layer);
        }
        let cache_index = cell * 3 + layer;
        if buffers.motion_layer_cache_generation[cache_index]
            == buffers.layer_cache_current_generation
        {
            return buffers.motion_layer_cache[cache_index];
        }
        let positions = self.cached_motion_positions_for_cell(
            buffers,
            left,
            right,
            left_seconds,
            right_seconds,
            cell,
            layer,
            buffers.motion_wind_cache[cache_index],
        );
        let (value, body_risk) = self.motion_cloud_layer_sample_at_positions_with_scalars(
            left, right, weight, layer, positions,
        );
        buffers.motion_layer_cache[cache_index] = value;
        buffers.motion_layer_cache_generation[cache_index] = buffers.layer_cache_current_generation;
        let scalar_base = cache_index * 2;
        buffers.motion_scalar_cache[scalar_base] = body_risk;
        buffers.motion_scalar_cache_generation[scalar_base] =
            buffers.layer_cache_current_generation;
        value
    }

    fn cached_motion_cloud_layers_for_cell(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        cell: usize,
    ) -> [CloudLayerSample; 3] {
        if cell >= self.cell_count {
            return [CloudLayerSample::default(); 3];
        }
        if let (Some(cache), Some(key_index)) =
            (buffers.shared_motion.as_ref(), buffers.shared_motion_index)
        {
            return cache.layers_for_cell(key_index, cell).unwrap_or_default();
        }
        self.cache_motion_winds_for_cell_all_layers(
            buffers,
            left,
            right,
            left_seconds,
            right_seconds,
            cell,
        );
        let cache_base = cell * 3;
        let active = [
            buffers.motion_state_cache[cache_base] != 0,
            buffers.motion_state_cache[cache_base + 1] != 0,
            buffers.motion_state_cache[cache_base + 2] != 0,
        ];
        std::array::from_fn(|layer| {
            if !active[layer] {
                return self.cached_cloud_layer_sample(buffers, left, right, weight, cell, layer);
            }
            let cache_index = cell * 3 + layer;
            if buffers.motion_layer_cache_generation[cache_index]
                == buffers.layer_cache_current_generation
            {
                return buffers.motion_layer_cache[cache_index];
            }
            let positions = self.cached_motion_positions_for_cell(
                buffers,
                left,
                right,
                left_seconds,
                right_seconds,
                cell,
                layer,
                buffers.motion_wind_cache[cache_index],
            );
            let (value, body_risk) = self.motion_cloud_layer_sample_at_positions_with_scalars(
                left, right, weight, layer, positions,
            );
            buffers.motion_layer_cache[cache_index] = value;
            buffers.motion_layer_cache_generation[cache_index] =
                buffers.layer_cache_current_generation;
            let scalar_base = cache_index * 2;
            buffers.motion_scalar_cache[scalar_base] = body_risk;
            buffers.motion_scalar_cache_generation[scalar_base] =
                buffers.layer_cache_current_generation;
            value
        })
    }

    #[cfg(test)]
    fn semi_lagrangian_family_scalar(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        lat: f32,
        lon: f32,
        layer: usize,
        field: usize,
    ) -> f32 {
        let (left_lat, left_lon) =
            self.wind_displaced_position(left, lat, lon, layer, left_seconds, -1.0);
        let (right_lat, right_lon) =
            self.wind_displaced_position(right, lat, lon, layer, right_seconds, 1.0);
        let sample_at = |time: usize, sample_lat: f32, sample_lon: f32| {
            let Some(horizontal) = self.cloud_horizontal_weights(sample_lat, sample_lon) else {
                return 0.0;
            };
            self.horizontal_at_time(time, &horizontal, field)
        };
        let weight = clamp01(weight);
        let left_value = sample_at(left, left_lat, left_lon);
        let right_value = sample_at(right, right_lat, right_lon);
        left_value + (right_value - left_value) * weight
    }

    fn motion_family_scalar_at_positions(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        field: usize,
        positions: [f32; 4],
    ) -> f32 {
        let left_lat = positions[0];
        let left_lon = positions[1];
        let right_lat = positions[2];
        let right_lon = positions[3];
        let sample_at = |time: usize, sample_lat: f32, sample_lon: f32| {
            let Some(horizontal) = self.cloud_horizontal_weights(sample_lat, sample_lon) else {
                return 0.0;
            };
            self.horizontal_at_time(time, &horizontal, field)
        };
        let left_value = sample_at(left, left_lat, left_lon);
        let right_value = sample_at(right, right_lat, right_lon);
        left_value + (right_value - left_value) * clamp01(weight)
    }

    fn cached_motion_family_scalar(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        lat: f32,
        lon: f32,
        layer: usize,
        field: usize,
    ) -> f32 {
        let Some(horizontal) = self.cloud_horizontal_weights(lat, lon) else {
            return 0.0;
        };
        let scalar_slot = match field {
            HIGH_TARGET_BODY_RISK | MID_TARGET_BODY_RISK | LOW_TARGET_BODY_RISK => 0,
            LOCAL_UPPER_SUPPORT => 1,
            _ => return 0.0,
        };
        if let (Some(cache), Some(key_index)) =
            (buffers.shared_motion.as_ref(), buffers.shared_motion_index)
        {
            let mut value = 0.0f32;
            for &(cell, horizontal_weight) in &horizontal {
                if horizontal_weight <= 0.0 || cell >= self.cell_count {
                    continue;
                }
                let Some(scalar) = cache.scalar(key_index, cell, layer, scalar_slot) else {
                    return 0.0;
                };
                value += horizontal_weight * scalar;
            }
            return value;
        }
        if !self.cached_motion_is_active_with_weights(
            buffers,
            left,
            right,
            left_seconds,
            right_seconds,
            &horizontal,
            layer,
        ) {
            return self.interpolated_with_horizontal(left, right, weight, &horizontal, field);
        }
        let mut value = 0.0f32;
        for &(cell, horizontal_weight) in &horizontal {
            if horizontal_weight <= 0.0 || cell >= self.cell_count || layer >= 3 {
                continue;
            }
            let cache_index = cell * 3 + layer;
            let scalar_index = cache_index * 2 + scalar_slot;
            if buffers.motion_scalar_cache_generation[scalar_index]
                != buffers.layer_cache_current_generation
            {
                let winds = self.cached_motion_winds_for_cell(
                    buffers,
                    left,
                    right,
                    left_seconds,
                    right_seconds,
                    cell,
                    layer,
                );
                let positions = self.cached_motion_positions_for_cell(
                    buffers,
                    left,
                    right,
                    left_seconds,
                    right_seconds,
                    cell,
                    layer,
                    winds,
                );
                buffers.motion_scalar_cache[scalar_index] =
                    self.motion_family_scalar_at_positions(left, right, weight, field, positions);
                buffers.motion_scalar_cache_generation[scalar_index] =
                    buffers.layer_cache_current_generation;
            }
            value += horizontal_weight * buffers.motion_scalar_cache[scalar_index];
        }
        value
    }

    fn cached_motion_family_scalar_for_exact_cell(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        lat: f32,
        lon: f32,
        cell: usize,
        layer: usize,
        field: usize,
    ) -> Option<f32> {
        let horizontal = self.cloud_horizontal_weights(lat, lon)?;
        if horizontal[0] != (cell, 1.0)
            || horizontal[1].1 != 0.0
            || horizontal[2].1 != 0.0
            || horizontal[3].1 != 0.0
        {
            return None;
        }
        let scalar_slot = match field {
            HIGH_TARGET_BODY_RISK | MID_TARGET_BODY_RISK | LOW_TARGET_BODY_RISK => 0,
            LOCAL_UPPER_SUPPORT => 1,
            _ => return None,
        };
        if let (Some(cache), Some(key_index)) =
            (buffers.shared_motion.as_ref(), buffers.shared_motion_index)
        {
            return cache.scalar(key_index, cell, layer, scalar_slot);
        }
        if !self.cached_motion_state_for_cell(
            buffers,
            left,
            right,
            left_seconds,
            right_seconds,
            cell,
            layer,
        ) {
            return Some(self.interpolated(left, right, weight, cell, field));
        }
        let cache_index = cell * 3 + layer;
        let scalar_index = cache_index * 2 + scalar_slot;
        if buffers.motion_scalar_cache_generation[scalar_index]
            != buffers.layer_cache_current_generation
        {
            let winds = self.cached_motion_winds_for_cell(
                buffers,
                left,
                right,
                left_seconds,
                right_seconds,
                cell,
                layer,
            );
            let positions = self.cached_motion_positions_for_cell(
                buffers,
                left,
                right,
                left_seconds,
                right_seconds,
                cell,
                layer,
                winds,
            );
            buffers.motion_scalar_cache[scalar_index] =
                self.motion_family_scalar_at_positions(left, right, weight, field, positions);
            buffers.motion_scalar_cache_generation[scalar_index] =
                buffers.layer_cache_current_generation;
        }
        Some(buffers.motion_scalar_cache[scalar_index])
    }

    #[allow(clippy::too_many_arguments)]
    fn observer_cloud_base_aerosol_optical_depth(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        observer_lat: f32,
        observer_lon: f32,
        observer_elevation_m: f32,
        target_distance_km: f32,
        target_bearing_deg: f32,
        cloud_base_m: f32,
        target_layer: usize,
        target_is_observer_cell: bool,
        buffers: &mut SkyBuffers,
    ) -> ObserverCloudBasePath {
        if cloud_base_m <= observer_elevation_m {
            // A cloud base below the observer is not a terrain hit. Keep the
            // layer in the angular compositor so it can still hide an upper
            // cloud, while its own burnable fraction remains zero.
            return ObserverCloudBasePath {
                optical_depth: f32::INFINITY,
                cloud_transmission: 1.0,
                terrain_blocked: false,
            };
        }
        let observer_inside = observer_lat >= self.lat0
            && observer_lat <= self.lat0 + self.spacing * (self.rows.saturating_sub(1)) as f32
            && observer_lon >= self.lon0
            && observer_lon <= self.lon0 + self.spacing * (self.cols.saturating_sub(1)) as f32;
        let observer_path_node = self.cached_horizontal_path_node(
            buffers,
            false,
            0,
            0.0,
            observer_lat,
            observer_lon,
            target_bearing_deg,
        );
        let observer_horizontal = observer_path_node.horizontal;
        if target_is_observer_cell {
            if !observer_inside {
                return ObserverCloudBasePath {
                    optical_depth: 0.0,
                    cloud_transmission: 1.0,
                    terrain_blocked: false,
                };
            }
            if buffers.aerosol_empty {
                return ObserverCloudBasePath {
                    optical_depth: 0.0,
                    cloud_transmission: 1.0,
                    terrain_blocked: false,
                };
            }
            // The observer cell is anchored at the explicit station altitude.
            // Keep model orography for remote terrain, but do not integrate a
            // high-altitude observer from a grid surface that lies below it.
            let terrain_m = observer_elevation_m;
            let aerosol = self.interpolated_aerosol_column(
                buffers,
                left,
                right,
                weight,
                &observer_horizontal,
            );
            let normalization = aerosol.normalization_integral(observer_elevation_m);
            return ObserverCloudBasePath {
                optical_depth: aerosol.vertical_optical_depth_with_normalization(
                    observer_elevation_m,
                    cloud_base_m,
                    terrain_m,
                    normalization,
                ),
                cloud_transmission: 1.0,
                terrain_blocked: false,
            };
        }

        let step_km = (self.spacing.abs() * 111.0).clamp(8.0, 35.0);
        let count = (target_distance_km / step_km).ceil().max(1.0) as usize;
        // The ray starts at the actual observer, not at the nearest model
        // orography. Remote cells remain terrain-blocking samples below.
        let observer_terrain_m = observer_elevation_m;
        let mut previous_extinction = if !buffers.aerosol_empty && observer_inside {
            self.cached_aerosol_extinction_at_path_node(
                buffers,
                false,
                0,
                left,
                right,
                weight,
                &observer_horizontal,
                observer_elevation_m,
                observer_terrain_m,
            )
        } else {
            0.0
        };
        let mut previous_distance_km = 0.0f32;
        let mut previous_ray_height_m = observer_elevation_m;
        let mut optical_depth = 0.0f32;
        let mut cloud_optical_depth = 0.0f32;
        let mut peak_cloud_blocking = 0.0f32;
        let mut previous_cell = None;
        let target_body_km = 0.75 * step_km;
        let target_distance_for_trig = target_distance_km.max(0.0);
        let (sin_target, cos_target) = (target_distance_for_trig / EARTH_RADIUS_KM).sin_cos();
        let line_of_sight = SphericalPathGeometry::with_target_trig(
            observer_elevation_m,
            cloud_base_m,
            target_distance_for_trig,
            sin_target,
            cos_target,
        );
        for index in 1..=count {
            let distance_from_observer = (index as f32 * step_km).min(target_distance_km);
            let sample_tangent = buffers
                .observer_horizontal_path_node(index)
                .map(|node| node.sample_tangent)
                .unwrap_or_else(|| (distance_from_observer / EARTH_RADIUS_KM).tan());
            let ray_height_m =
                line_of_sight.height_m_with_tangent(distance_from_observer, sample_tangent);
            let node = self.cached_horizontal_path_node(
                buffers,
                false,
                index,
                distance_from_observer,
                observer_lat,
                observer_lon,
                target_bearing_deg,
            );
            let distance_from_observer = node.distance_km;
            let Some(cell) = node.cell else {
                break;
            };
            let terrain_m = self.cached_terrain_elevation(buffers, left, right, weight, cell);
            // Terrain is an occluding surface rather than a radiance field.
            // Keep the nearest resolved ridge for a hard horizon veto while
            // the atmospheric/cloud quantities use bilinear interpolation.
            if ray_height_m <= terrain_m + 25.0 {
                return ObserverCloudBasePath {
                    optical_depth: f32::INFINITY,
                    cloud_transmission: 0.0,
                    terrain_blocked: true,
                };
            }
            let horizontal = node.horizontal;
            if !buffers.aerosol_empty {
                let current_extinction = self.cached_aerosol_extinction_at_path_node(
                    buffers,
                    false,
                    index,
                    left,
                    right,
                    weight,
                    &horizontal,
                    ray_height_m,
                    terrain_m,
                );
                let horizontal_km = distance_from_observer - previous_distance_km;
                let vertical_km = (ray_height_m - previous_ray_height_m) / 1000.0;
                optical_depth += 0.5
                    * (previous_extinction + current_extinction)
                    * horizontal_km.hypot(vertical_km);
                previous_extinction = current_extinction;
            }
            if previous_cell != Some(cell) {
                previous_cell = Some(cell);
                if target_distance_km - distance_from_observer > target_body_km {
                    let midpoint_distance_km =
                        0.5 * (previous_distance_km + distance_from_observer);
                    let (midpoint_horizontal, midpoint_tangent) = self
                        .cached_observer_midpoint_geometry(
                            buffers,
                            index,
                            previous_distance_km,
                            distance_from_observer,
                            observer_lat,
                            observer_lon,
                            target_bearing_deg,
                        );
                    let midpoint_ray_height_m =
                        line_of_sight.height_m_with_tangent(midpoint_distance_km, midpoint_tangent);
                    let blocking = self.cloud_blocking_with_horizontal(
                        left,
                        right,
                        weight,
                        left_seconds,
                        right_seconds,
                        &midpoint_horizontal,
                        midpoint_ray_height_m,
                        None,
                        1.0,
                        Some(target_layer),
                        Some((false, index)),
                        buffers,
                    );
                    peak_cloud_blocking = peak_cloud_blocking.max(blocking);
                    if blocking > 0.0 {
                        cloud_optical_depth += -((1.0 - blocking).clamp(0.02, 1.0)).ln();
                    }
                }
            }
            previous_distance_km = distance_from_observer;
            previous_ray_height_m = ray_height_m;
        }
        ObserverCloudBasePath {
            optical_depth: optical_depth.max(0.0),
            cloud_transmission: (-cloud_optical_depth.max(0.0)).exp()
                * (1.0 - 0.72 * clamp01(peak_cloud_blocking)).max(0.0),
            terrain_blocked: false,
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn observer_cloud_base_aerosol_optical_depths(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        observer_lat: f32,
        observer_lon: f32,
        observer_elevation_m: f32,
        target_distance_km: f32,
        target_bearing_deg: f32,
        clouds: [CloudLayerSample; 3],
        target_is_observer_cell: bool,
        buffers: &mut SkyBuffers,
    ) -> [ObserverCloudBasePath; 3] {
        let mut paths = [ObserverCloudBasePath::default(); 3];
        let mut active = [false; 3];
        for layer in 0..3 {
            let cloud = clouds[layer];
            if cloud.amount < 0.001 || cloud.top_m <= observer_elevation_m {
                continue;
            }
            active[layer] = true;
            if cloud.base_m <= observer_elevation_m {
                // Match the single-layer path's below-observer contract. The
                // layer still participates in angular composition, but it is
                // not traced from the observer to its own base.
                paths[layer] = ObserverCloudBasePath {
                    optical_depth: f32::INFINITY,
                    cloud_transmission: 1.0,
                    terrain_blocked: false,
                };
            }
        }
        if !active.iter().any(|value| *value) {
            return paths;
        }

        let observer_inside = observer_lat >= self.lat0
            && observer_lat <= self.lat0 + self.spacing * (self.rows.saturating_sub(1)) as f32
            && observer_lon >= self.lon0
            && observer_lon <= self.lon0 + self.spacing * (self.cols.saturating_sub(1)) as f32;
        let observer_path_node = self.cached_horizontal_path_node(
            buffers,
            false,
            0,
            0.0,
            observer_lat,
            observer_lon,
            target_bearing_deg,
        );
        let observer_horizontal = observer_path_node.horizontal;
        if target_is_observer_cell {
            if !observer_inside {
                return paths;
            }
            if buffers.aerosol_empty {
                return paths;
            }
            // The aerosol column and its normalization are shared by all
            // three vertical integrations at the observer cell.
            let aerosol = self.interpolated_aerosol_column(
                buffers,
                left,
                right,
                weight,
                &observer_horizontal,
            );
            let normalization = aerosol.normalization_integral(observer_elevation_m);
            for layer in 0..3 {
                if active[layer] && clouds[layer].base_m > observer_elevation_m {
                    paths[layer] = ObserverCloudBasePath {
                        optical_depth: aerosol.vertical_optical_depth_with_normalization(
                            observer_elevation_m,
                            clouds[layer].base_m,
                            observer_elevation_m,
                            normalization,
                        ),
                        cloud_transmission: 1.0,
                        terrain_blocked: false,
                    };
                }
            }
            return paths;
        }

        let step_km = (self.spacing.abs() * 111.0).clamp(8.0, 35.0);
        let count = (target_distance_km / step_km).ceil().max(1.0) as usize;
        // All layers use the same observer-to-target horizontal path. Only
        // the line-of-sight height and the cloud layer being excluded differ.
        let observer_terrain_m = observer_elevation_m;
        let initial_extinction = if !buffers.aerosol_empty
            && observer_inside
            && (0..3).any(|layer| active[layer] && clouds[layer].base_m > observer_elevation_m)
        {
            self.cached_aerosol_extinction_at_path_node(
                buffers,
                false,
                0,
                left,
                right,
                weight,
                &observer_horizontal,
                observer_elevation_m,
                observer_terrain_m,
            )
        } else {
            0.0
        };
        let mut previous_extinction = [0.0f32; 3];
        let mut previous_distance_km = [0.0f32; 3];
        let mut previous_ray_height_m = [observer_elevation_m; 3];
        let mut optical_depth = [0.0f32; 3];
        let mut cloud_optical_depth = [0.0f32; 3];
        let mut peak_cloud_blocking = [0.0f32; 3];
        let mut previous_cell = [None; 3];
        let mut done: [bool; 3] = std::array::from_fn(|layer| !active[layer]);
        for layer in 0..3 {
            if active[layer] && clouds[layer].base_m > observer_elevation_m {
                previous_extinction[layer] = initial_extinction;
            } else {
                done[layer] = true;
            }
        }
        let target_distance_for_trig = target_distance_km.max(0.0);
        let (sin_target, cos_target) = (target_distance_for_trig / EARTH_RADIUS_KM).sin_cos();
        let line_of_sight: [SphericalPathGeometry; 3] = std::array::from_fn(|layer| {
            SphericalPathGeometry::with_target_trig(
                observer_elevation_m,
                clouds[layer].base_m,
                target_distance_for_trig,
                sin_target,
                cos_target,
            )
        });
        let target_body_km = 0.75 * step_km;

        for index in 1..=count {
            if done.iter().all(|value| *value) {
                break;
            }
            let distance_from_observer = (index as f32 * step_km).min(target_distance_km);
            let sample_tangent = buffers
                .observer_horizontal_path_node(index)
                .map(|node| node.sample_tangent)
                .unwrap_or_else(|| (distance_from_observer / EARTH_RADIUS_KM).tan());
            let node = self.cached_horizontal_path_node(
                buffers,
                false,
                index,
                distance_from_observer,
                observer_lat,
                observer_lon,
                target_bearing_deg,
            );
            let distance_from_observer = node.distance_km;
            let Some(cell) = node.cell else {
                break;
            };
            let terrain_m = self.cached_terrain_elevation(buffers, left, right, weight, cell);
            let horizontal = node.horizontal;
            let ray_heights_m = std::array::from_fn(|layer| {
                line_of_sight[layer].height_m_with_tangent(distance_from_observer, sample_tangent)
            });
            let current_extinctions = if !buffers.aerosol_empty {
                self.cached_aerosol_extinction_at_path_node_layers(
                    buffers,
                    false,
                    index,
                    left,
                    right,
                    weight,
                    &horizontal,
                    ray_heights_m,
                    std::array::from_fn(|layer| {
                        !done[layer]
                            && ray_heights_m[layer].is_finite()
                            && ray_heights_m[layer] > terrain_m + 25.0
                    }),
                    terrain_m,
                )
            } else {
                [0.0; 3]
            };
            for layer in 0..3 {
                if done[layer] {
                    continue;
                }
                let ray_height_m = ray_heights_m[layer];
                if ray_height_m <= terrain_m + 25.0 {
                    paths[layer] = ObserverCloudBasePath {
                        optical_depth: f32::INFINITY,
                        cloud_transmission: 0.0,
                        terrain_blocked: true,
                    };
                    done[layer] = true;
                    continue;
                }
                let previous_distance = previous_distance_km[layer];
                let previous_ray_height = previous_ray_height_m[layer];
                if !buffers.aerosol_empty {
                    let current_extinction = current_extinctions[layer];
                    let horizontal_km = distance_from_observer - previous_distance;
                    let vertical_km = (ray_height_m - previous_ray_height) / 1000.0;
                    optical_depth[layer] += 0.5
                        * (previous_extinction[layer] + current_extinction)
                        * horizontal_km.hypot(vertical_km);
                    previous_extinction[layer] = current_extinction;
                }

                if previous_cell[layer] == Some(cell) {
                    previous_distance_km[layer] = distance_from_observer;
                    previous_ray_height_m[layer] = ray_height_m;
                    continue;
                }
                previous_cell[layer] = Some(cell);
                if target_distance_km - distance_from_observer <= target_body_km {
                    previous_distance_km[layer] = distance_from_observer;
                    previous_ray_height_m[layer] = ray_height_m;
                    continue;
                }
                let midpoint_distance_km = 0.5 * (previous_distance + distance_from_observer);
                let (midpoint_horizontal, midpoint_tangent) = self
                    .cached_observer_midpoint_geometry(
                        buffers,
                        index,
                        previous_distance,
                        distance_from_observer,
                        observer_lat,
                        observer_lon,
                        target_bearing_deg,
                    );
                let midpoint_ray_height_m = line_of_sight[layer]
                    .height_m_with_tangent(midpoint_distance_km, midpoint_tangent);
                let blocking = self.cloud_blocking_with_horizontal(
                    left,
                    right,
                    weight,
                    left_seconds,
                    right_seconds,
                    &midpoint_horizontal,
                    midpoint_ray_height_m,
                    None,
                    1.0,
                    Some(layer),
                    Some((false, index)),
                    buffers,
                );
                peak_cloud_blocking[layer] = peak_cloud_blocking[layer].max(blocking);
                if blocking > 0.0 {
                    cloud_optical_depth[layer] += -((1.0 - blocking).clamp(0.02, 1.0)).ln();
                }
                previous_distance_km[layer] = distance_from_observer;
                previous_ray_height_m[layer] = ray_height_m;
            }
        }

        for layer in 0..3 {
            if !active[layer] || done[layer] {
                continue;
            }
            paths[layer] = ObserverCloudBasePath {
                optical_depth: optical_depth[layer].max(0.0),
                cloud_transmission: (-cloud_optical_depth[layer].max(0.0)).exp()
                    * (1.0 - 0.72 * clamp01(peak_cloud_blocking[layer])).max(0.0),
                terrain_blocked: false,
            };
        }
        paths
    }

    fn cloud_layer_sample(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        cell: usize,
        layer: usize,
    ) -> CloudLayerSample {
        let finite = |value: f32| if value.is_finite() { value } else { 0.0 };
        let (
            amount_field,
            opacity_field,
            material_field,
            base_field,
            top_field,
            pressure_low,
            pressure_high,
        ) = match layer {
            0 => (
                HIGH,
                HIGH_BLOCKER_OPACITY,
                HIGH_MATERIAL,
                HIGH_BASE,
                HIGH_TOP,
                0.0,
                HIGH_CLOUD_MAX_PRESSURE_HPA,
            ),
            1 => (
                MID,
                MID_BLOCKER_OPACITY,
                MID_MATERIAL,
                MID_BASE,
                MID_TOP,
                HIGH_CLOUD_MAX_PRESSURE_HPA,
                MID_CLOUD_MAX_PRESSURE_HPA,
            ),
            _ => (
                LOW,
                LOW_BLOCKER_OPACITY,
                LOW_MATERIAL,
                LOW_BASE,
                LOW_TOP,
                700.0,
                1100.0,
            ),
        };
        let amount = clamp01(finite(self.interpolated(
            left,
            right,
            weight,
            cell,
            amount_field,
        )));
        if amount <= 0.0 {
            return CloudLayerSample::default();
        }
        let mut base_m = finite(self.interpolated(left, right, weight, cell, base_field));
        let mut top_m = finite(self.interpolated(left, right, weight, cell, top_field));
        let selected_pressure = finite(self.interpolated(left, right, weight, cell, PRESSURE));
        if top_m <= base_m && selected_pressure > pressure_low && selected_pressure <= pressure_high
        {
            base_m = finite(self.interpolated(left, right, weight, cell, BASE));
            top_m = finite(self.interpolated(left, right, weight, cell, TOP));
        }
        if top_m <= base_m {
            top_m = base_m
                + match layer {
                    0 => 1200.0,
                    1 => 900.0,
                    _ => 600.0,
                };
        }
        CloudLayerSample {
            amount,
            opacity: clamp01(finite(self.interpolated(
                left,
                right,
                weight,
                cell,
                opacity_field,
            ))),
            // Python derives independent material quality for each resolved
            // cloud family. The native ray tracer consumes the matching
            // family field so disconnected layers cannot share a penalty.
            material_quality: clamp01(finite(self.interpolated(
                left,
                right,
                weight,
                cell,
                material_field,
            ))),
            base_m,
            top_m,
        }
    }

    fn cached_cloud_layer_sample(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        cell: usize,
        layer: usize,
    ) -> CloudLayerSample {
        if cell < self.cell_count && layer < 3 {
            let cache_index = cell * 3 + layer;
            if buffers.layer_cache_generation[cache_index] == buffers.layer_cache_current_generation
            {
                return buffers.layer_cache[cache_index];
            }
            let value = self.cloud_layer_sample(left, right, weight, cell, layer);
            buffers.layer_cache[cache_index] = value;
            buffers.layer_cache_generation[cache_index] = buffers.layer_cache_current_generation;
            return value;
        }
        self.cloud_layer_sample(left, right, weight, cell, layer)
    }

    #[cfg(test)]
    fn cached_horizontal_cloud_layer_sample(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        lat: f32,
        lon: f32,
        layer: usize,
    ) -> CloudLayerSample {
        let horizontal = self.horizontal_weights(lat, lon);
        self.cached_horizontal_cloud_layer_sample_with_weights(
            buffers,
            left,
            right,
            weight,
            &horizontal,
            layer,
        )
    }

    fn cached_horizontal_cloud_layer_sample_with_weights(
        &self,
        buffers: &mut SkyBuffers,
        left: usize,
        right: usize,
        weight: f32,
        horizontal: &[(usize, f32); 4],
        layer: usize,
    ) -> CloudLayerSample {
        let mut amount = 0.0f32;
        let mut material_weight = 0.0f32;
        let mut opacity = 0.0f32;
        let mut material_quality = 0.0f32;
        let mut base_m = 0.0f32;
        let mut top_m = 0.0f32;
        for &(cell, horizontal_weight) in horizontal {
            if horizontal_weight <= 0.0 {
                continue;
            }
            let cloud = self.cached_cloud_layer_sample(buffers, left, right, weight, cell, layer);
            amount += horizontal_weight * cloud.amount;
            if cloud.amount > 0.0 {
                material_weight += horizontal_weight * cloud.amount;
                opacity += horizontal_weight * cloud.amount * cloud.opacity;
                material_quality += horizontal_weight * cloud.amount * cloud.material_quality;
                base_m += horizontal_weight * cloud.amount * cloud.base_m;
                top_m += horizontal_weight * cloud.amount * cloud.top_m;
            }
        }
        if material_weight <= 1e-6 {
            return CloudLayerSample::default();
        }
        CloudLayerSample {
            amount: clamp01(amount),
            opacity: clamp01(opacity / material_weight),
            material_quality: clamp01(material_quality / material_weight),
            base_m: base_m / material_weight,
            top_m: top_m / material_weight,
        }
    }

    fn cloud_blocking_with_horizontal(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        horizontal: &[(usize, f32); 4],
        ray_height_m: f32,
        target_extension_layer: Option<usize>,
        target_extension_factor: f32,
        excluded_layer: Option<usize>,
        path_cache: Option<(bool, usize)>,
        buffers: &mut SkyBuffers,
    ) -> f32 {
        if let Some((true, path_index)) = path_cache {
            if let (Some(cache), Some(key_index), Some(origin_cell)) = (
                buffers.selected_shared_solar_path_samples(),
                buffers.shared_solar_path_sample_index,
                buffers.shared_solar_path_cell,
            ) {
                if buffers.shared_solar_path_bearing_matches {
                    if let Some(cached_layers) =
                        cache.cloud_layers_ref(key_index, origin_cell, path_index)
                    {
                        return Self::cloud_blocking_from_shared_layers(
                            cached_layers,
                            ray_height_m,
                            target_extension_layer,
                            target_extension_factor,
                            excluded_layer,
                        );
                    }
                }
            }
        }
        let mut transmission = 1.0f32;
        if let Some((solar, path_index)) = path_cache {
            let cached_layers = self.cached_motion_cloud_layers_at_path_node(
                buffers,
                solar,
                path_index,
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                horizontal,
            );
            return self.cloud_blocking_from_layers(
                cached_layers,
                ray_height_m,
                target_extension_layer,
                target_extension_factor,
                excluded_layer,
            );
        }
        for layer in 0..3 {
            if excluded_layer == Some(layer) {
                continue;
            }
            let cloud = self.cached_motion_cloud_layer_sample_with_weights(
                buffers,
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                horizontal,
                layer,
            );
            if cloud.amount <= 0.0 || cloud.top_m <= cloud.base_m {
                continue;
            }
            let edge_softness = match layer {
                0 => 450.0,
                1 => 300.0,
                _ => 180.0,
            };
            let intersection = if ray_height_m >= cloud.base_m && ray_height_m <= cloud.top_m {
                1.0
            } else if ray_height_m < cloud.base_m {
                smooth_gate(ray_height_m, cloud.base_m - edge_softness, cloud.base_m)
            } else {
                1.0 - smooth_gate(ray_height_m, cloud.top_m, cloud.top_m + edge_softness)
            };
            let target_extension_factor = if target_extension_layer == Some(layer) {
                target_extension_factor
            } else {
                1.0
            };
            let blocker = cloud_blocking_factor(cloud, intersection, target_extension_factor);
            transmission *= 1.0 - blocker;
        }
        1.0 - transmission
    }

    fn cloud_blocking_from_shared_layers(
        layers: &[SharedCloudLayerSample; 3],
        ray_height_m: f32,
        target_extension_layer: Option<usize>,
        target_extension_factor: f32,
        excluded_layer: Option<usize>,
    ) -> f32 {
        let mut transmission = 1.0f32;
        for layer in 0..3 {
            if excluded_layer == Some(layer) {
                continue;
            }
            let cloud = layers[layer];
            if cloud.amount <= 0.0 || cloud.top_m <= cloud.base_m {
                continue;
            }
            let edge_softness = match layer {
                0 => 450.0,
                1 => 300.0,
                _ => 180.0,
            };
            let intersection = if ray_height_m >= cloud.base_m && ray_height_m <= cloud.top_m {
                1.0
            } else if ray_height_m < cloud.base_m {
                smooth_gate(ray_height_m, cloud.base_m - edge_softness, cloud.base_m)
            } else {
                1.0 - smooth_gate(ray_height_m, cloud.top_m, cloud.top_m + edge_softness)
            };
            let target_extension_factor = if target_extension_layer == Some(layer) {
                target_extension_factor
            } else {
                1.0
            };
            let blocker = cloud_blocking_factor_values(
                cloud.amount,
                cloud.opacity,
                intersection,
                target_extension_factor,
            );
            transmission *= 1.0 - blocker;
        }
        1.0 - transmission
    }

    fn cloud_blocking_from_layers(
        &self,
        layers: [CloudLayerSample; 3],
        ray_height_m: f32,
        target_extension_layer: Option<usize>,
        target_extension_factor: f32,
        excluded_layer: Option<usize>,
    ) -> f32 {
        let mut transmission = 1.0f32;
        for layer in 0..3 {
            if excluded_layer == Some(layer) {
                continue;
            }
            let cloud = layers[layer];
            if cloud.amount <= 0.0 || cloud.top_m <= cloud.base_m {
                continue;
            }
            let edge_softness = match layer {
                0 => 450.0,
                1 => 300.0,
                _ => 180.0,
            };
            let intersection = if ray_height_m >= cloud.base_m && ray_height_m <= cloud.top_m {
                1.0
            } else if ray_height_m < cloud.base_m {
                smooth_gate(ray_height_m, cloud.base_m - edge_softness, cloud.base_m)
            } else {
                1.0 - smooth_gate(ray_height_m, cloud.top_m, cloud.top_m + edge_softness)
            };
            let target_extension_factor = if target_extension_layer == Some(layer) {
                target_extension_factor
            } else {
                1.0
            };
            let blocker = cloud_blocking_factor(cloud, intersection, target_extension_factor);
            transmission *= 1.0 - blocker;
        }
        1.0 - transmission
    }

    #[cfg(target_arch = "aarch64")]
    #[inline(always)]
    unsafe fn cloud_blocking_from_layers_for_targets_neon(
        layers: [CloudLayerSample; 3],
        ray_heights_m: [f32; 3],
        target_extension_layers: [Option<usize>; 3],
        target_extension_factors: [f32; 3],
    ) -> [f32; 3] {
        use std::arch::aarch64::*;

        let padded_heights = [ray_heights_m[0], ray_heights_m[1], ray_heights_m[2], 0.0];
        let heights = vld1q_f32(padded_heights.as_ptr());
        let zeros = vdupq_n_f32(0.0);
        let ones = vdupq_n_f32(1.0);
        let two = vdupq_n_f32(2.0);
        let three = vdupq_n_f32(3.0);
        let mut transmission = ones;

        for blocker_layer in 0..3 {
            let cloud = layers[blocker_layer];
            if cloud.amount <= 0.0 || cloud.top_m <= cloud.base_m {
                continue;
            }
            let edge_softness = match blocker_layer {
                0 => 450.0,
                1 => 300.0,
                _ => 180.0,
            };
            let base = vdupq_n_f32(cloud.base_m);
            let top = vdupq_n_f32(cloud.top_m);
            let low = vdupq_n_f32(cloud.base_m - edge_softness);
            let high = vdupq_n_f32(cloud.top_m + edge_softness);
            let inside = vandq_u32(vcgeq_f32(heights, base), vcleq_f32(heights, top));
            let below = vcltq_f32(heights, base);
            let below_t = vdivq_f32(vsubq_f32(heights, low), vsubq_f32(base, low));
            let below_t = vmaxq_f32(zeros, vminq_f32(ones, below_t));
            let below_gate = vmulq_f32(
                vmulq_f32(below_t, below_t),
                vsubq_f32(three, vmulq_f32(two, below_t)),
            );

            let above_t = vdivq_f32(vsubq_f32(heights, top), vsubq_f32(high, top));
            let above_t = vmaxq_f32(zeros, vminq_f32(ones, above_t));
            let above_gate = vmulq_f32(
                vmulq_f32(above_t, above_t),
                vsubq_f32(three, vmulq_f32(two, above_t)),
            );
            let outside = vbslq_f32(below, below_gate, vsubq_f32(ones, above_gate));
            let intersection = vbslq_f32(inside, ones, outside);

            let extension = [
                if target_extension_layers[0] == Some(blocker_layer) {
                    target_extension_factors[0]
                } else {
                    1.0
                },
                if target_extension_layers[1] == Some(blocker_layer) {
                    target_extension_factors[1]
                } else {
                    1.0
                },
                if target_extension_layers[2] == Some(blocker_layer) {
                    target_extension_factors[2]
                } else {
                    1.0
                },
                1.0,
            ];
            let extension = vld1q_f32(extension.as_ptr());
            let amount_opacity = vdupq_n_f32(cloud.amount * cloud.opacity);
            let blocker = vmulq_f32(vmulq_f32(amount_opacity, intersection), extension);
            let blocker = vmaxq_f32(zeros, vminq_f32(ones, blocker));
            transmission = vmulq_f32(transmission, vsubq_f32(ones, blocker));
        }

        let mut values = [0.0f32; 4];
        vst1q_f32(values.as_mut_ptr(), vsubq_f32(ones, transmission));
        [values[0], values[1], values[2]]
    }

    #[cfg(target_arch = "aarch64")]
    #[inline(always)]
    unsafe fn cloud_blocking_from_shared_layers_for_targets_neon(
        layers: &[SharedCloudLayerSample; 3],
        ray_heights_m: [f32; 3],
        target_extension_layers: [Option<usize>; 3],
        target_extension_factors: [f32; 3],
    ) -> [f32; 3] {
        use std::arch::aarch64::*;

        let padded_heights = [ray_heights_m[0], ray_heights_m[1], ray_heights_m[2], 0.0];
        let heights = vld1q_f32(padded_heights.as_ptr());
        let zeros = vdupq_n_f32(0.0);
        let ones = vdupq_n_f32(1.0);
        let two = vdupq_n_f32(2.0);
        let three = vdupq_n_f32(3.0);
        let mut transmission = ones;

        for (blocker_layer, cloud) in layers.iter().copied().enumerate() {
            if cloud.amount <= 0.0 || cloud.top_m <= cloud.base_m {
                continue;
            }
            let edge_softness = match blocker_layer {
                0 => 450.0,
                1 => 300.0,
                _ => 180.0,
            };
            let base = vdupq_n_f32(cloud.base_m);
            let top = vdupq_n_f32(cloud.top_m);
            let low = vdupq_n_f32(cloud.base_m - edge_softness);
            let high = vdupq_n_f32(cloud.top_m + edge_softness);
            let inside = vandq_u32(vcgeq_f32(heights, base), vcleq_f32(heights, top));
            let below = vcltq_f32(heights, base);
            let below_t = vdivq_f32(vsubq_f32(heights, low), vsubq_f32(base, low));
            let below_t = vmaxq_f32(zeros, vminq_f32(ones, below_t));
            let below_gate = vmulq_f32(
                vmulq_f32(below_t, below_t),
                vsubq_f32(three, vmulq_f32(two, below_t)),
            );

            let above_t = vdivq_f32(vsubq_f32(heights, top), vsubq_f32(high, top));
            let above_t = vmaxq_f32(zeros, vminq_f32(ones, above_t));
            let above_gate = vmulq_f32(
                vmulq_f32(above_t, above_t),
                vsubq_f32(three, vmulq_f32(two, above_t)),
            );
            let outside = vbslq_f32(below, below_gate, vsubq_f32(ones, above_gate));
            let intersection = vbslq_f32(inside, ones, outside);
            let extension = [
                if target_extension_layers[0] == Some(blocker_layer) {
                    target_extension_factors[0]
                } else {
                    1.0
                },
                if target_extension_layers[1] == Some(blocker_layer) {
                    target_extension_factors[1]
                } else {
                    1.0
                },
                if target_extension_layers[2] == Some(blocker_layer) {
                    target_extension_factors[2]
                } else {
                    1.0
                },
                1.0,
            ];
            let extension = vld1q_f32(extension.as_ptr());
            let amount_opacity = vdupq_n_f32(cloud.amount * cloud.opacity);
            let blocker = vmulq_f32(vmulq_f32(amount_opacity, intersection), extension);
            let blocker = vmaxq_f32(zeros, vminq_f32(ones, blocker));
            transmission = vmulq_f32(transmission, vsubq_f32(ones, blocker));
        }

        let mut values = [0.0f32; 4];
        vst1q_f32(values.as_mut_ptr(), vsubq_f32(ones, transmission));
        [values[0], values[1], values[2]]
    }

    #[inline(always)]
    fn cloud_blocking_from_shared_layers_for_targets(
        layers: &[SharedCloudLayerSample; 3],
        ray_heights_m: [f32; 3],
        target_extension_layers: [Option<usize>; 3],
        target_extension_factors: [f32; 3],
        active_targets: [bool; 3],
    ) -> [f32; 3] {
        #[cfg(target_arch = "aarch64")]
        if active_targets == [true; 3] && *SOLAR_BLOCKING_NEON_ENABLED {
            return unsafe {
                Self::cloud_blocking_from_shared_layers_for_targets_neon(
                    layers,
                    ray_heights_m,
                    target_extension_layers,
                    target_extension_factors,
                )
            };
        }
        let mut transmission = [1.0f32; 3];
        for (blocker_layer, cloud) in layers.iter().copied().enumerate() {
            if cloud.amount <= 0.0 || cloud.top_m <= cloud.base_m {
                continue;
            }
            let edge_softness = match blocker_layer {
                0 => 450.0,
                1 => 300.0,
                _ => 180.0,
            };
            for target_layer in 0..3 {
                if !active_targets[target_layer] {
                    continue;
                }
                let ray_height_m = ray_heights_m[target_layer];
                let intersection = if ray_height_m >= cloud.base_m && ray_height_m <= cloud.top_m {
                    1.0
                } else if ray_height_m < cloud.base_m {
                    smooth_gate(ray_height_m, cloud.base_m - edge_softness, cloud.base_m)
                } else {
                    1.0 - smooth_gate(ray_height_m, cloud.top_m, cloud.top_m + edge_softness)
                };
                let target_extension_factor =
                    if target_extension_layers[target_layer] == Some(blocker_layer) {
                        target_extension_factors[target_layer]
                    } else {
                        1.0
                    };
                let blocker = cloud_blocking_factor_values(
                    cloud.amount,
                    cloud.opacity,
                    intersection,
                    target_extension_factor,
                );
                transmission[target_layer] *= 1.0 - blocker;
            }
        }
        std::array::from_fn(|layer| 1.0 - transmission[layer])
    }

    #[inline]
    fn cloud_blocking_from_layers_for_targets(
        layers: [CloudLayerSample; 3],
        ray_heights_m: [f32; 3],
        target_extension_layers: [Option<usize>; 3],
        target_extension_factors: [f32; 3],
        active_targets: [bool; 3],
    ) -> [f32; 3] {
        #[cfg(target_arch = "aarch64")]
        if active_targets == [true; 3] && *SOLAR_BLOCKING_NEON_ENABLED {
            // The fourth lane is padding. The scalar `powf` and optical-depth
            // accumulation remain in the caller so the existing operation
            // order is unchanged after the intersection/blocker stage.
            return unsafe {
                Self::cloud_blocking_from_layers_for_targets_neon(
                    layers,
                    ray_heights_m,
                    target_extension_layers,
                    target_extension_factors,
                )
            };
        }
        let mut transmission = [1.0f32; 3];
        if active_targets == [true; 3] {
            for blocker_layer in 0..3 {
                let cloud = layers[blocker_layer];
                if cloud.amount <= 0.0 || cloud.top_m <= cloud.base_m {
                    continue;
                }
                let edge_softness = match blocker_layer {
                    0 => 450.0,
                    1 => 300.0,
                    _ => 180.0,
                };
                let ray_height_m = ray_heights_m[0];
                let intersection = if ray_height_m >= cloud.base_m && ray_height_m <= cloud.top_m {
                    1.0
                } else if ray_height_m < cloud.base_m {
                    smooth_gate(ray_height_m, cloud.base_m - edge_softness, cloud.base_m)
                } else {
                    1.0 - smooth_gate(ray_height_m, cloud.top_m, cloud.top_m + edge_softness)
                };
                let target_extension_factor = if target_extension_layers[0] == Some(blocker_layer) {
                    target_extension_factors[0]
                } else {
                    1.0
                };
                let blocker = cloud_blocking_factor(cloud, intersection, target_extension_factor);
                transmission[0] *= 1.0 - blocker;

                let ray_height_m = ray_heights_m[1];
                let intersection = if ray_height_m >= cloud.base_m && ray_height_m <= cloud.top_m {
                    1.0
                } else if ray_height_m < cloud.base_m {
                    smooth_gate(ray_height_m, cloud.base_m - edge_softness, cloud.base_m)
                } else {
                    1.0 - smooth_gate(ray_height_m, cloud.top_m, cloud.top_m + edge_softness)
                };
                let target_extension_factor = if target_extension_layers[1] == Some(blocker_layer) {
                    target_extension_factors[1]
                } else {
                    1.0
                };
                let blocker = cloud_blocking_factor(cloud, intersection, target_extension_factor);
                transmission[1] *= 1.0 - blocker;

                let ray_height_m = ray_heights_m[2];
                let intersection = if ray_height_m >= cloud.base_m && ray_height_m <= cloud.top_m {
                    1.0
                } else if ray_height_m < cloud.base_m {
                    smooth_gate(ray_height_m, cloud.base_m - edge_softness, cloud.base_m)
                } else {
                    1.0 - smooth_gate(ray_height_m, cloud.top_m, cloud.top_m + edge_softness)
                };
                let target_extension_factor = if target_extension_layers[2] == Some(blocker_layer) {
                    target_extension_factors[2]
                } else {
                    1.0
                };
                let blocker = cloud_blocking_factor(cloud, intersection, target_extension_factor);
                transmission[2] *= 1.0 - blocker;
            }
            return std::array::from_fn(|layer| 1.0 - transmission[layer]);
        }
        for blocker_layer in 0..3 {
            let cloud = layers[blocker_layer];
            if cloud.amount <= 0.0 || cloud.top_m <= cloud.base_m {
                continue;
            }
            let edge_softness = match blocker_layer {
                0 => 450.0,
                1 => 300.0,
                _ => 180.0,
            };
            for target_layer in 0..3 {
                if !active_targets[target_layer] {
                    continue;
                }
                let ray_height_m = ray_heights_m[target_layer];
                let intersection = if ray_height_m >= cloud.base_m && ray_height_m <= cloud.top_m {
                    1.0
                } else if ray_height_m < cloud.base_m {
                    smooth_gate(ray_height_m, cloud.base_m - edge_softness, cloud.base_m)
                } else {
                    1.0 - smooth_gate(ray_height_m, cloud.top_m, cloud.top_m + edge_softness)
                };
                let target_extension_factor =
                    if target_extension_layers[target_layer] == Some(blocker_layer) {
                        target_extension_factors[target_layer]
                    } else {
                        1.0
                    };
                let blocker = cloud_blocking_factor(cloud, intersection, target_extension_factor);
                transmission[target_layer] *= 1.0 - blocker;
            }
        }
        std::array::from_fn(|layer| 1.0 - transmission[layer])
    }

    #[allow(clippy::too_many_arguments)]
    #[cfg(test)]
    fn solar_cloud_base_lights(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        target_lat: f32,
        target_lon: f32,
        target_distance_km: f32,
        target_sector_deg: f32,
        active: [bool; 3],
        clouds: [CloudLayerSample; 3],
        target_deep_risks: [f32; 3],
        solar_elevation: f32,
        solar_azimuth: f32,
        target_terrain_override_m: Option<f32>,
        buffers: &mut SkyBuffers,
    ) -> [SolarCloudBaseLight; 3] {
        let solar_illumination_elevation_rad =
            solar_illumination_elevation_deg(solar_elevation).to_radians();
        let target_sector_cos = target_sector_deg.to_radians().cos();
        self.solar_cloud_base_lights_with_illumination(
            left,
            right,
            weight,
            left_seconds,
            right_seconds,
            target_lat,
            target_lon,
            target_distance_km,
            target_sector_deg,
            target_sector_cos,
            active,
            clouds,
            target_deep_risks,
            solar_elevation,
            solar_illumination_elevation_rad,
            solar_azimuth,
            target_terrain_override_m,
            buffers,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn solar_cloud_base_lights_with_illumination(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        target_lat: f32,
        target_lon: f32,
        target_distance_km: f32,
        target_sector_deg: f32,
        target_sector_cos: f32,
        active: [bool; 3],
        clouds: [CloudLayerSample; 3],
        target_deep_risks: [f32; 3],
        solar_elevation: f32,
        solar_illumination_elevation_rad: f32,
        solar_azimuth: f32,
        target_terrain_override_m: Option<f32>,
        buffers: &mut SkyBuffers,
    ) -> [SolarCloudBaseLight; 3] {
        let has_valid_active_layer = active.iter().enumerate().any(|(layer, enabled)| {
            *enabled
                && clouds[layer].base_m.is_finite()
                && clouds[layer].top_m.is_finite()
                && clouds[layer].top_m > clouds[layer].base_m
        });
        if !has_valid_active_layer {
            return [SolarCloudBaseLight::default(); 3];
        }
        if !buffers.audit_enabled
            && !buffers.solar_path_exact_cache_enabled
            && !buffers.solar_path_template_cache_enabled
        {
            buffers.invalidate_solar_path_template();
            return self.solar_cloud_base_lights_uncached(
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                target_lat,
                target_lon,
                target_distance_km,
                target_sector_deg,
                target_sector_cos,
                active,
                clouds,
                target_deep_risks,
                solar_elevation,
                solar_illumination_elevation_rad,
                solar_azimuth,
                target_terrain_override_m,
                buffers,
            );
        }
        let template = if !buffers.audit_enabled && buffers.solar_path_exact_cache_enabled {
            self.solar_path_exact_key(
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                target_lat,
                target_lon,
                solar_azimuth,
                target_terrain_override_m,
            )
            .or_else(|| {
                buffers
                    .solar_path_template_cache_enabled
                    .then(|| {
                        self.solar_path_template_key(
                            left,
                            right,
                            weight,
                            left_seconds,
                            right_seconds,
                            target_lat,
                            target_lon,
                            solar_azimuth,
                            target_terrain_override_m,
                        )
                    })
                    .flatten()
            })
        } else if buffers.solar_path_template_cache_enabled && !buffers.audit_enabled {
            self.solar_path_template_key(
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                target_lat,
                target_lon,
                solar_azimuth,
                target_terrain_override_m,
            )
        } else {
            None
        };
        let (template_key, path_bearing) = template
            .map(|(key, bearing)| (Some(key), bearing))
            .unwrap_or((None, solar_azimuth));
        let template_loaded = if let Some(key) = template_key {
            buffers.load_solar_path_template(key)
        } else {
            buffers.invalidate_solar_path_template();
            false
        };
        let computed_lights = self.solar_cloud_base_lights_uncached(
            left,
            right,
            weight,
            left_seconds,
            right_seconds,
            target_lat,
            target_lon,
            target_distance_km,
            target_sector_deg,
            target_sector_cos,
            active,
            clouds,
            target_deep_risks,
            solar_elevation,
            solar_illumination_elevation_rad,
            path_bearing,
            target_terrain_override_m,
            buffers,
        );
        if (buffers.solar_path_exact_cache_enabled || buffers.solar_path_template_cache_enabled)
            && !buffers.audit_enabled
            && !template_loaded
        {
            if let Some(key) = template_key {
                if self.complete_solar_path_template(
                    left,
                    right,
                    weight,
                    left_seconds,
                    right_seconds,
                    target_lat,
                    target_lon,
                    path_bearing,
                    buffers,
                ) {
                    buffers.store_solar_path_template(key);
                }
            }
        }
        computed_lights
    }

    #[allow(clippy::too_many_arguments)]
    fn solar_cloud_base_lights_uncached(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        target_lat: f32,
        target_lon: f32,
        target_distance_km: f32,
        target_sector_deg: f32,
        target_sector_cos: f32,
        active: [bool; 3],
        clouds: [CloudLayerSample; 3],
        target_deep_risks: [f32; 3],
        solar_elevation: f32,
        solar_illumination_elevation_rad: f32,
        solar_azimuth: f32,
        target_terrain_override_m: Option<f32>,
        buffers: &mut SkyBuffers,
    ) -> [SolarCloudBaseLight; 3] {
        buffers.shared_solar_path_aerosol_enabled = target_terrain_override_m.is_none();
        let mut lights = [SolarCloudBaseLight::default(); 3];
        if active.iter().filter(|value| **value).count() == 1 {
            let layer = active.iter().position(|value| *value).unwrap();
            lights[layer] = self.solar_cloud_base_light_uncached(
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                target_lat,
                target_lon,
                target_distance_km,
                target_sector_deg,
                target_sector_cos,
                layer,
                clouds[layer].base_m,
                clouds[layer].top_m,
                clouds[layer].amount,
                clouds[layer].opacity,
                target_deep_risks[layer],
                solar_elevation,
                solar_illumination_elevation_rad,
                solar_azimuth,
                target_terrain_override_m,
                if target_terrain_override_m.is_none() {
                    Some(match layer {
                        HIGH => clouds[MID],
                        MID => clouds[HIGH],
                        _ => CloudLayerSample::default(),
                    })
                } else {
                    None
                },
                buffers,
            );
            return lights;
        }
        let mut active = active;
        for layer in 0..3 {
            if active[layer]
                && (!clouds[layer].base_m.is_finite()
                    || !clouds[layer].top_m.is_finite()
                    || clouds[layer].top_m <= clouds[layer].base_m)
            {
                active[layer] = false;
            }
        }
        if !active.iter().any(|value| *value) {
            return lights;
        }

        let directional_distance = target_distance_km * target_sector_cos;
        let local_solar_elevation =
            solar_illumination_elevation_rad + directional_distance / 6371.0;
        let solar_ray_slope = local_solar_elevation.tan();
        let step_km = (self.spacing.abs() * 111.0).clamp(8.0, 35.0);
        let max_trace_km = 1000.0f32.min(self.radius_km + 400.0);
        let count = (max_trace_km / step_km).ceil() as usize;
        let target_path_node = self.cached_horizontal_path_node(
            buffers,
            true,
            0,
            0.0,
            target_lat,
            target_lon,
            solar_azimuth,
        );
        let target_cell = target_path_node.cell;
        let target_horizontal = target_path_node.horizontal;
        let shared_cloud_cache_key = target_cell.and_then(|_| {
            self.solar_path_cloud_cache_key(
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                target_lat,
                target_lon,
                solar_azimuth,
                target_terrain_override_m,
                buffers,
            )
        });
        let shared_cloud_cache_loaded = shared_cloud_cache_key
            .map(|key| buffers.load_solar_path_cloud_cache(key))
            .unwrap_or(false);
        let target_terrain_m = target_terrain_override_m.unwrap_or_else(|| {
            self.interpolated_with_horizontal(
                left,
                right,
                weight,
                &target_horizontal,
                TERRAIN_ELEVATION,
            )
        });
        let target_thickness: [f32; 3] =
            std::array::from_fn(|layer| (clouds[layer].top_m - clouds[layer].base_m).max(0.0));
        let adjacent_layers = [clouds[1], clouds[0], CloudLayerSample::default()];
        let target_layer_deep_risk: [f32; 3] = std::array::from_fn(|layer| {
            if active[layer] {
                target_layer_self_shielding_risk(
                    layer,
                    target_deep_risks[layer],
                    clouds[layer].base_m,
                    clouds[layer].top_m,
                    target_thickness[layer],
                    clouds[layer].amount,
                    adjacent_layers[layer],
                )
            } else {
                0.0
            }
        });
        let target_self_transmission: [f32; 3] = std::array::from_fn(|layer| {
            if active[layer] && solar_elevation <= 0.0 {
                deep_target_cloud_self_transmission(
                    clouds[layer].opacity,
                    target_layer_deep_risk[layer],
                )
            } else {
                1.0
            }
        });
        let mut previous_aerosol_extinction = [0.0f32; 3];
        if !buffers.aerosol_empty && target_cell.is_some() {
            let initial_extinctions = self.cached_aerosol_extinction_at_path_node_layers(
                buffers,
                true,
                0,
                left,
                right,
                weight,
                &target_horizontal,
                std::array::from_fn(|layer| clouds[layer].base_m),
                active,
                target_terrain_m,
            );
            for layer in 0..3 {
                if active[layer] {
                    previous_aerosol_extinction[layer] = initial_extinctions[layer];
                }
            }
        }
        let mut cloud_optical_depth = [0.0f32; 3];
        let mut aerosol_optical_depth = [0.0f32; 3];
        let mut peak_blocking = [0.0f32; 3];
        let mut target_deck_connected = [true; 3];
        let mut target_deck_disconnected_run = [0u32; 3];
        let mut traced_cloud_cells = [0usize; 3];
        let mut attenuated_target_cells = [0usize; 3];
        let mut clear_gap_count = [0usize; 3];
        let mut terrain_blocked = [false; 3];
        let mut opaque_cutoff = [false; 3];
        let mut previous_distance_km = [0.0f32; 3];
        let mut previous_ray_height_m: [f32; 3] = std::array::from_fn(|layer| clouds[layer].base_m);
        let mut previous_cell = [None; 3];
        let mut done: [bool; 3] = std::array::from_fn(|layer| !active[layer]);
        let mut audit_trace: [Option<SolarPathTrace>; 3] =
            std::array::from_fn(|_| buffers.audit_enabled.then(SolarPathTrace::default));

        for index in 1..=count {
            let distance_from_target = (index as f32 * step_km).min(max_trace_km);
            let curvature = distance_from_target * distance_from_target * 1000.0 / (2.0 * 6371.0);
            let solar_height_term = solar_ray_slope * distance_from_target * 1000.0;
            let mut ray_height_m = [0.0f32; 3];
            let mut needs_node = false;
            for layer in 0..3 {
                if done[layer] {
                    continue;
                }
                if let Some(trace) = audit_trace[layer].as_mut() {
                    trace.trace_steps += 1;
                }
                ray_height_m[layer] = clouds[layer].base_m + solar_height_term + curvature;
                // Cheap whole-ray lower bound: once the straight ray dips
                // below the MSL sphere it is underground for every surface at
                // or above sea level. The target cell's own terrain is NOT a
                // valid bound (downslope terrain under a rising ray is clear);
                // signed below-sea-level basins keep their target bound. Each
                // sampled node still applies its own terrain check.
                let terrain_floor_m = target_terrain_m.min(0.0);
                if ray_height_m[layer] <= terrain_floor_m + 25.0 {
                    lights[layer] = SolarCloudBaseLight {
                        audit: path_audit_record(
                            audit_trace[layer].as_ref(),
                            cloud_optical_depth[layer],
                            target_self_transmission[layer],
                            peak_blocking[layer],
                            false,
                            false,
                            attenuated_target_cells[layer],
                            target_cell,
                            clouds[layer].opacity,
                        ),
                        ..SolarCloudBaseLight::default()
                    };
                    terrain_blocked[layer] = true;
                    done[layer] = true;
                    continue;
                }
                if ray_height_m[layer] >= 20_000.0 {
                    if let Some(trace) = audit_trace[layer].as_mut() {
                        trace.ray_height_break = true;
                    }
                    done[layer] = true;
                    continue;
                }
                needs_node = true;
            }
            if !needs_node {
                break;
            }

            let node = self.cached_horizontal_path_node(
                buffers,
                true,
                index,
                distance_from_target,
                target_lat,
                target_lon,
                solar_azimuth,
            );
            let distance_from_target = node.distance_km;
            let Some(cell) = node.cell else {
                for layer in 0..3 {
                    if !done[layer] {
                        if let Some(trace) = audit_trace[layer].as_mut() {
                            trace.domain_break = true;
                        }
                        done[layer] = true;
                    }
                }
                break;
            };
            let terrain_m = self.cached_terrain_elevation(buffers, left, right, weight, cell);
            let current_extinctions = if !buffers.aerosol_empty {
                self.cached_aerosol_extinction_at_path_node_layers(
                    buffers,
                    true,
                    index,
                    left,
                    right,
                    weight,
                    &node.horizontal,
                    ray_height_m,
                    std::array::from_fn(|layer| {
                        !done[layer]
                            && ray_height_m[layer].is_finite()
                            && ray_height_m[layer] > terrain_m + 25.0
                    }),
                    terrain_m,
                )
            } else {
                [0.0; 3]
            };
            for layer in 0..3 {
                if done[layer] {
                    continue;
                }
                if let Some(trace) = audit_trace[layer].as_mut() {
                    trace.record_cell(cell);
                }
                if ray_height_m[layer] <= terrain_m + 25.0 {
                    lights[layer] = SolarCloudBaseLight {
                        audit: path_audit_record(
                            audit_trace[layer].as_ref(),
                            cloud_optical_depth[layer],
                            target_self_transmission[layer],
                            peak_blocking[layer],
                            false,
                            false,
                            attenuated_target_cells[layer],
                            target_cell,
                            clouds[layer].opacity,
                        ),
                        ..SolarCloudBaseLight::default()
                    };
                    terrain_blocked[layer] = true;
                    done[layer] = true;
                    continue;
                }
                if !buffers.aerosol_empty {
                    let current_aerosol_extinction = current_extinctions[layer];
                    let horizontal_km = distance_from_target - previous_distance_km[layer];
                    let vertical_km = (ray_height_m[layer] - previous_ray_height_m[layer]) / 1000.0;
                    let segment_km = horizontal_km.hypot(vertical_km);
                    aerosol_optical_depth[layer] += 0.5
                        * (previous_aerosol_extinction[layer] + current_aerosol_extinction)
                        * segment_km;
                    previous_distance_km[layer] = distance_from_target;
                    previous_ray_height_m[layer] = ray_height_m[layer];
                    previous_aerosol_extinction[layer] = current_aerosol_extinction;
                }
            }

            let mut target_extension_layers = [None; 3];
            let mut target_extension_factors = [1.0f32; 3];
            let mut pending_targets = [false; 3];
            for layer in 0..3 {
                if done[layer] || previous_cell[layer] == Some(cell) {
                    continue;
                }
                previous_cell[layer] = Some(cell);
                // Path nodes start one full step from the target, so no node
                // can fall inside the target body; the former distance check
                // here was dead code.
                pending_targets[layer] = true;
            }
            if pending_targets.iter().any(|value| *value) {
                let mut apply_extensions = |extensions: &[CloudLayerSample; 3]| {
                    for layer in 0..3 {
                        if !pending_targets[layer] {
                            continue;
                        }
                        let same_vertical_layer = target_deck_extension_is_connected(
                            extensions[layer],
                            clouds[layer].base_m,
                            clouds[layer].top_m,
                        );
                        if target_deck_connected[layer] {
                            if extensions[layer].amount < TARGET_DECK_HARD_CLEAR_AMOUNT {
                                // A real hole in the deck: break immediately.
                                target_deck_connected[layer] = false;
                                clear_gap_count[layer] += 1;
                            } else if same_vertical_layer {
                                target_deck_disconnected_run[layer] = 0;
                            } else {
                                target_deck_disconnected_run[layer] += 1;
                                if target_deck_disconnected_run[layer]
                                    >= TARGET_DECK_BREAK_RUN_CELLS
                                {
                                    target_deck_connected[layer] = false;
                                    clear_gap_count[layer] += 1;
                                }
                            }
                        }
                        let target_deck_self_shielding = solar_elevation <= 0.0
                            && target_deck_connected[layer]
                            && target_deck_can_share_self_shielding(
                                target_thickness[layer],
                                clouds[layer].opacity,
                                target_layer_deep_risk[layer],
                            );
                        target_extension_layers[layer] =
                            target_deck_self_shielding.then_some(layer);
                        target_extension_factors[layer] = if target_deck_self_shielding {
                            target_extension_factor_at_distance(distance_from_target)
                        } else {
                            1.0
                        };
                        traced_cloud_cells[layer] += 1;
                        if target_extension_layers[layer].is_some() {
                            attenuated_target_cells[layer] += 1;
                        }
                    }
                };
                let shared_extensions = buffers
                    .shared_motion
                    .as_ref()
                    .and_then(|cache| buffers.shared_motion_index.map(|key| (cache, key)))
                    .and_then(|(cache, key_index)| cache.layers_for_cell_ref(key_index, cell));
                if let Some(extensions) = shared_extensions {
                    apply_extensions(extensions);
                } else {
                    let extensions = self.cached_motion_cloud_layers_for_cell(
                        buffers,
                        left,
                        right,
                        weight,
                        left_seconds,
                        right_seconds,
                        cell,
                    );
                    apply_extensions(&extensions);
                }
                let blockings =
                    if let Some(layers_at_path) = buffers.shared_solar_path_cloud_layers(index) {
                        Self::cloud_blocking_from_shared_layers_for_targets(
                            layers_at_path,
                            ray_height_m,
                            target_extension_layers,
                            target_extension_factors,
                            pending_targets,
                        )
                    } else {
                        let layers_at_path = self.cached_motion_cloud_layers_at_path_node(
                            buffers,
                            true,
                            index,
                            left,
                            right,
                            weight,
                            left_seconds,
                            right_seconds,
                            &node.horizontal,
                        );
                        Self::cloud_blocking_from_layers_for_targets(
                            layers_at_path,
                            ray_height_m,
                            target_extension_layers,
                            target_extension_factors,
                            pending_targets,
                        )
                    };
                for layer in 0..3 {
                    if !pending_targets[layer] {
                        continue;
                    }
                    let blocking = blockings[layer];
                    cloud_optical_depth[layer] += cloud_optical_depth_increment(blocking, step_km);
                    peak_blocking[layer] = peak_blocking[layer].max(blocking);
                    if peak_blocking[layer] >= 0.995 || cloud_optical_depth[layer] >= 7.0 {
                        lights[layer] = SolarCloudBaseLight {
                            cloud_transmission: 0.0,
                            geometry_reachable: true,
                            path_cloud_transmission_before_target_self: 0.0,
                            aerosol_optical_depth: aerosol_optical_depth[layer].max(0.0),
                            target_deck_connected: if target_deck_connected[layer] {
                                1.0
                            } else {
                                0.0
                            },
                            target_deck_attenuated_fraction: attenuated_target_cells[layer] as f32
                                / traced_cloud_cells[layer].max(1) as f32,
                            cloud_optical_depth: cloud_optical_depth[layer].max(0.0),
                            peak_blocking: clamp01(peak_blocking[layer]),
                            clear_gap_count: clear_gap_count[layer] as f32,
                            audit: path_audit_record(
                                audit_trace[layer].as_ref(),
                                cloud_optical_depth[layer],
                                target_self_transmission[layer],
                                peak_blocking[layer],
                                true,
                                true,
                                attenuated_target_cells[layer],
                                target_cell,
                                clouds[layer].opacity,
                            ),
                        };
                        opaque_cutoff[layer] = true;
                        done[layer] = true;
                    }
                }
            }
            if done.iter().all(|value| *value) {
                break;
            }
        }

        for layer in 0..3 {
            if !active[layer] || !lights[layer].cloud_transmission.is_finite() {
                continue;
            }
            if terrain_blocked[layer] || opaque_cutoff[layer] {
                continue;
            }
            if target_self_transmission[layer] < 1.0 {
                cloud_optical_depth[layer] = add_target_self_optical_depth(
                    cloud_optical_depth[layer],
                    target_self_transmission[layer],
                );
            }
            let cloud_transmission =
                cloud_transmission_from_path(cloud_optical_depth[layer], peak_blocking[layer]);
            let target_self_tau = if target_self_transmission[layer] < 1.0 {
                -target_self_transmission[layer].clamp(0.02, 1.0).ln()
            } else {
                0.0
            };
            let path_cloud_transmission_before_target_self = cloud_transmission_from_path(
                if target_self_transmission[layer] < 1.0 {
                    cloud_optical_depth[layer] - target_self_tau
                } else {
                    cloud_optical_depth[layer]
                },
                peak_blocking[layer],
            );
            lights[layer] = SolarCloudBaseLight {
                cloud_transmission: clamp01(cloud_transmission),
                geometry_reachable: true,
                path_cloud_transmission_before_target_self: clamp01(
                    path_cloud_transmission_before_target_self,
                ),
                aerosol_optical_depth: aerosol_optical_depth[layer].max(0.0),
                target_deck_connected: if target_deck_connected[layer] {
                    1.0
                } else {
                    0.0
                },
                target_deck_attenuated_fraction: attenuated_target_cells[layer] as f32
                    / traced_cloud_cells[layer].max(1) as f32,
                cloud_optical_depth: cloud_optical_depth[layer].max(0.0),
                peak_blocking: clamp01(peak_blocking[layer]),
                clear_gap_count: clear_gap_count[layer] as f32,
                audit: path_audit_record(
                    audit_trace[layer].as_ref(),
                    cloud_optical_depth[layer] - target_self_tau,
                    target_self_transmission[layer],
                    peak_blocking[layer],
                    true,
                    false,
                    attenuated_target_cells[layer],
                    target_cell,
                    clouds[layer].opacity,
                ),
            };
        }
        if let Some(key) = shared_cloud_cache_key {
            if !shared_cloud_cache_loaded {
                buffers.store_solar_path_cloud_cache(key, self.solar_path_node_count());
            }
        }
        lights
    }

    #[cfg(test)]
    #[allow(clippy::too_many_arguments)]
    fn solar_cloud_base_light(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        target_lat: f32,
        target_lon: f32,
        target_distance_km: f32,
        target_sector_deg: f32,
        target_layer: usize,
        cloud_base_m: f32,
        cloud_top_m: f32,
        target_amount: f32,
        target_opacity: f32,
        target_deep_risk: f32,
        solar_elevation: f32,
        solar_azimuth: f32,
        target_terrain_override_m: Option<f32>,
        buffers: &mut SkyBuffers,
    ) -> SolarCloudBaseLight {
        let template = if !buffers.audit_enabled && buffers.solar_path_exact_cache_enabled {
            self.solar_path_exact_key(
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                target_lat,
                target_lon,
                solar_azimuth,
                target_terrain_override_m,
            )
            .or_else(|| {
                buffers
                    .solar_path_template_cache_enabled
                    .then(|| {
                        self.solar_path_template_key(
                            left,
                            right,
                            weight,
                            left_seconds,
                            right_seconds,
                            target_lat,
                            target_lon,
                            solar_azimuth,
                            target_terrain_override_m,
                        )
                    })
                    .flatten()
            })
        } else if buffers.solar_path_template_cache_enabled && !buffers.audit_enabled {
            self.solar_path_template_key(
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                target_lat,
                target_lon,
                solar_azimuth,
                target_terrain_override_m,
            )
        } else {
            None
        };
        let (template_key, path_bearing) = template
            .map(|(key, bearing)| (Some(key), bearing))
            .unwrap_or((None, solar_azimuth));
        let template_loaded = template_key
            .map(|key| buffers.load_solar_path_template(key))
            .unwrap_or(false);
        let solar_illumination_elevation_rad =
            solar_illumination_elevation_deg(solar_elevation).to_radians();
        let light = self.solar_cloud_base_light_uncached(
            left,
            right,
            weight,
            left_seconds,
            right_seconds,
            target_lat,
            target_lon,
            target_distance_km,
            target_sector_deg,
            target_sector_deg.to_radians().cos(),
            target_layer,
            cloud_base_m,
            cloud_top_m,
            target_amount,
            target_opacity,
            target_deep_risk,
            solar_elevation,
            solar_illumination_elevation_rad,
            path_bearing,
            target_terrain_override_m,
            None,
            buffers,
        );
        if (buffers.solar_path_exact_cache_enabled || buffers.solar_path_template_cache_enabled)
            && !buffers.audit_enabled
            && !template_loaded
        {
            if let Some(key) = template_key {
                if self.complete_solar_path_template(
                    left,
                    right,
                    weight,
                    left_seconds,
                    right_seconds,
                    target_lat,
                    target_lon,
                    path_bearing,
                    buffers,
                ) {
                    buffers.store_solar_path_template(key);
                }
            }
        }
        light
    }

    #[allow(clippy::too_many_arguments)]
    fn solar_cloud_base_light_uncached(
        &self,
        left: usize,
        right: usize,
        weight: f32,
        left_seconds: f32,
        right_seconds: f32,
        target_lat: f32,
        target_lon: f32,
        target_distance_km: f32,
        _target_sector_deg: f32,
        target_sector_cos: f32,
        target_layer: usize,
        cloud_base_m: f32,
        cloud_top_m: f32,
        target_amount: f32,
        target_opacity: f32,
        target_deep_risk: f32,
        solar_elevation: f32,
        solar_illumination_elevation_rad: f32,
        solar_azimuth: f32,
        target_terrain_override_m: Option<f32>,
        adjacent_layer_override: Option<CloudLayerSample>,
        buffers: &mut SkyBuffers,
    ) -> SolarCloudBaseLight {
        buffers.shared_solar_path_aerosol_enabled = target_terrain_override_m.is_none();
        if !cloud_base_m.is_finite() || !cloud_top_m.is_finite() || cloud_top_m <= cloud_base_m {
            return SolarCloudBaseLight::default();
        }
        let directional_distance = target_distance_km * target_sector_cos;
        let local_solar_elevation =
            solar_illumination_elevation_rad + directional_distance / 6371.0;
        let solar_ray_slope = local_solar_elevation.tan();
        let step_km = (self.spacing.abs() * 111.0).clamp(8.0, 35.0);
        let max_trace_km = 1000.0f32.min(self.radius_km + 400.0);
        let count = (max_trace_km / step_km).ceil() as usize;
        let mut cloud_optical_depth = 0.0f32;
        let mut aerosol_optical_depth = 0.0f32;
        let mut peak_blocking = 0.0f32;
        let mut target_deck_connected = true;
        let mut target_deck_disconnected_run = 0u32;
        let mut traced_cloud_cells = 0usize;
        let mut attenuated_target_cells = 0usize;
        let mut clear_gap_count = 0usize;
        let mut audit_trace = if buffers.audit_enabled {
            Some(SolarPathTrace::default())
        } else {
            None
        };
        let mut opaque_cutoff = false;
        let target_path_node = self.cached_horizontal_path_node(
            buffers,
            true,
            0,
            0.0,
            target_lat,
            target_lon,
            solar_azimuth,
        );
        let target_cell = target_path_node.cell;
        let target_horizontal = target_path_node.horizontal;
        let shared_cloud_cache_key = target_cell.and_then(|_| {
            self.solar_path_cloud_cache_key(
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                target_lat,
                target_lon,
                solar_azimuth,
                target_terrain_override_m,
                buffers,
            )
        });
        let shared_cloud_cache_loaded = shared_cloud_cache_key
            .map(|key| buffers.load_solar_path_cloud_cache(key))
            .unwrap_or(false);
        let target_thickness_m = (cloud_top_m - cloud_base_m).max(0.0);
        let adjacent_layer = if !target_layer_self_shielding_needs_adjacent(
            target_layer,
            target_thickness_m,
            target_amount,
        ) {
            CloudLayerSample::default()
        } else {
            adjacent_layer_override.unwrap_or_else(|| {
                if target_layer == 0 {
                    self.cached_motion_cloud_layer_sample(
                        buffers,
                        left,
                        right,
                        weight,
                        left_seconds,
                        right_seconds,
                        target_lat,
                        target_lon,
                        1,
                    )
                } else if target_layer == 1 {
                    self.cached_motion_cloud_layer_sample(
                        buffers,
                        left,
                        right,
                        weight,
                        left_seconds,
                        right_seconds,
                        target_lat,
                        target_lon,
                        0,
                    )
                } else {
                    CloudLayerSample::default()
                }
            })
        };
        let target_layer_deep_risk = target_layer_self_shielding_risk(
            target_layer,
            target_deep_risk,
            cloud_base_m,
            cloud_top_m,
            target_thickness_m,
            target_amount,
            adjacent_layer,
        );
        let target_self_transmission = if solar_elevation <= 0.0 {
            deep_target_cloud_self_transmission(target_opacity, target_layer_deep_risk)
        } else {
            1.0
        };
        let target_terrain_m = target_terrain_override_m.unwrap_or_else(|| {
            self.interpolated_with_horizontal(
                left,
                right,
                weight,
                &target_horizontal,
                TERRAIN_ELEVATION,
            )
        });
        let mut previous_aerosol_extinction = if !buffers.aerosol_empty && target_cell.is_some() {
            self.cached_aerosol_extinction_at_path_node(
                buffers,
                true,
                0,
                left,
                right,
                weight,
                &target_horizontal,
                cloud_base_m,
                target_terrain_m,
            )
        } else {
            0.0
        };
        let mut previous_distance_km = 0.0f32;
        let mut previous_ray_height_m = cloud_base_m;
        let mut previous_cell = None;
        for index in 1..=count {
            if let Some(trace) = audit_trace.as_mut() {
                trace.trace_steps += 1;
            }
            // ``count`` is rounded up so the final sample can otherwise step
            // past the declared trace radius and outside the prepared model
            // domain. Keep the endpoint inside the same radius used to size
            // the GFS/CAMS footprint.
            let distance_from_target = (index as f32 * step_km).min(max_trace_km);
            let curvature = distance_from_target * distance_from_target * 1000.0 / (2.0 * 6371.0);
            let solar_height_term = solar_ray_slope * distance_from_target * 1000.0;
            let ray_height_m = cloud_base_m + solar_height_term + curvature;
            // Cheap whole-ray lower bound: below the MSL sphere the straight
            // ray is underground for every surface at or above sea level. The
            // target cell's own terrain is NOT a valid bound (a downslope ray
            // stays clear of remote terrain); signed basins keep their target
            // bound. Remote nodes still apply their own terrain check.
            let terrain_floor_m = target_terrain_m.min(0.0);
            if ray_height_m <= terrain_floor_m + 25.0 {
                return SolarCloudBaseLight {
                    audit: path_audit_record(
                        audit_trace.as_ref(),
                        cloud_optical_depth,
                        target_self_transmission,
                        peak_blocking,
                        false,
                        opaque_cutoff,
                        attenuated_target_cells,
                        target_cell,
                        target_opacity,
                    ),
                    ..SolarCloudBaseLight::default()
                };
            }
            if ray_height_m >= 20_000.0 {
                if let Some(trace) = audit_trace.as_mut() {
                    trace.ray_height_break = true;
                }
                break;
            }
            let node = self.cached_horizontal_path_node(
                buffers,
                true,
                index,
                distance_from_target,
                target_lat,
                target_lon,
                solar_azimuth,
            );
            let distance_from_target = node.distance_km;
            let Some(cell) = node.cell else {
                if let Some(trace) = audit_trace.as_mut() {
                    trace.domain_break = true;
                }
                break;
            };
            if let Some(trace) = audit_trace.as_mut() {
                trace.record_cell(cell);
            }
            // A terrain ridge must remain an occluding surface. Bilinear
            // averaging here could lower a narrow ridge below the ray and
            // leak a small amount of direct light through it.
            let terrain_m = self.cached_terrain_elevation(buffers, left, right, weight, cell);
            if ray_height_m <= terrain_m + 25.0 {
                return SolarCloudBaseLight {
                    audit: path_audit_record(
                        audit_trace.as_ref(),
                        cloud_optical_depth,
                        target_self_transmission,
                        peak_blocking,
                        false,
                        opaque_cutoff,
                        attenuated_target_cells,
                        target_cell,
                        target_opacity,
                    ),
                    ..SolarCloudBaseLight::default()
                };
            }
            // AOD is a column optical depth, not an opaque surface. Normalize
            // the interpolated CAMS aerext532 profile to that column and
            // integrate the actual segment length once.
            let horizontal = node.horizontal;
            if !buffers.aerosol_empty {
                let current_aerosol_extinction = self.cached_aerosol_extinction_at_path_node(
                    buffers,
                    true,
                    index,
                    left,
                    right,
                    weight,
                    &horizontal,
                    ray_height_m,
                    terrain_m,
                );
                let horizontal_km = distance_from_target - previous_distance_km;
                let vertical_km = (ray_height_m - previous_ray_height_m) / 1000.0;
                let segment_km = horizontal_km.hypot(vertical_km);
                aerosol_optical_depth +=
                    0.5 * (previous_aerosol_extinction + current_aerosol_extinction) * segment_km;
                previous_distance_km = distance_from_target;
                previous_ray_height_m = ray_height_m;
                previous_aerosol_extinction = current_aerosol_extinction;
            }
            if previous_cell == Some(cell) {
                continue;
            }
            previous_cell = Some(cell);
            // Same dead-code note as the batch path: nodes start one full
            // step from the target.
            // Connectivity is a resolved model-deck diagnostic. Preserve a
            // clear model cell as a real gap; bilinear cloud amounts are used
            // for the optical blocker below, not to erase this gap.
            let target_extension = self.cached_motion_cloud_layer_sample_for_cell(
                buffers,
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                cell,
                target_layer,
            );
            let same_vertical_layer =
                target_deck_extension_is_connected(target_extension, cloud_base_m, cloud_top_m);
            if target_deck_connected {
                if target_extension.amount < TARGET_DECK_HARD_CLEAR_AMOUNT {
                    // A real hole in the deck: break immediately.
                    target_deck_connected = false;
                    clear_gap_count += 1;
                } else if same_vertical_layer {
                    target_deck_disconnected_run = 0;
                } else {
                    target_deck_disconnected_run += 1;
                    if target_deck_disconnected_run >= TARGET_DECK_BREAK_RUN_CELLS {
                        target_deck_connected = false;
                        clear_gap_count += 1;
                    }
                }
            }
            let target_thickness_m = cloud_top_m - cloud_base_m;
            let target_deck_self_shielding = solar_elevation <= 0.0
                && target_deck_connected
                && target_deck_can_share_self_shielding(
                    target_thickness_m,
                    target_opacity,
                    target_layer_deep_risk,
                );
            let target_extension_layer = if target_deck_self_shielding {
                Some(target_layer)
            } else {
                None
            };
            traced_cloud_cells += 1;
            if target_extension_layer.is_some() {
                attenuated_target_cells += 1;
            }
            let blocking = self.cloud_blocking_with_horizontal(
                left,
                right,
                weight,
                left_seconds,
                right_seconds,
                &horizontal,
                ray_height_m,
                target_extension_layer,
                if target_extension_layer.is_some() {
                    target_extension_factor_at_distance(distance_from_target)
                } else {
                    1.0
                },
                None,
                Some((true, index)),
                buffers,
            );
            // Keep exact endpoint results out of the transcendental path;
            // negative or NaN inputs retain the original powf semantics.
            cloud_optical_depth += cloud_optical_depth_increment(blocking, step_km);
            peak_blocking = peak_blocking.max(blocking);
            if peak_blocking >= 0.995 || cloud_optical_depth >= 7.0 {
                opaque_cutoff = true;
                return SolarCloudBaseLight {
                    cloud_transmission: 0.0,
                    geometry_reachable: true,
                    path_cloud_transmission_before_target_self: 0.0,
                    aerosol_optical_depth: aerosol_optical_depth.max(0.0),
                    target_deck_connected: if target_deck_connected { 1.0 } else { 0.0 },
                    target_deck_attenuated_fraction: attenuated_target_cells as f32
                        / traced_cloud_cells.max(1) as f32,
                    cloud_optical_depth: cloud_optical_depth.max(0.0),
                    peak_blocking: clamp01(peak_blocking),
                    clear_gap_count: clear_gap_count as f32,
                    audit: path_audit_record(
                        audit_trace.as_ref(),
                        cloud_optical_depth,
                        target_self_transmission,
                        peak_blocking,
                        true,
                        opaque_cutoff,
                        attenuated_target_cells,
                        target_cell,
                        target_opacity,
                    ),
                };
            }
        }
        if target_self_transmission < 1.0 {
            cloud_optical_depth =
                add_target_self_optical_depth(cloud_optical_depth, target_self_transmission);
            // The target body's transmission is already represented by the
            // optical-depth integral above. Do not feed the same body into
            // the unresolved-path peak correction a second time.
        }
        let cloud_transmission = cloud_transmission_from_path(cloud_optical_depth, peak_blocking);
        let path_cloud_transmission_before_target_self = cloud_transmission_from_path(
            if target_self_transmission < 1.0 {
                cloud_optical_depth - (-target_self_transmission.clamp(0.02, 1.0).ln())
            } else {
                cloud_optical_depth
            },
            peak_blocking,
        );
        let target_self_tau = if target_self_transmission < 1.0 {
            -target_self_transmission.clamp(0.02, 1.0).ln()
        } else {
            0.0
        };
        let light = SolarCloudBaseLight {
            cloud_transmission: clamp01(cloud_transmission),
            geometry_reachable: true,
            path_cloud_transmission_before_target_self: clamp01(
                path_cloud_transmission_before_target_self,
            ),
            aerosol_optical_depth: aerosol_optical_depth.max(0.0),
            target_deck_connected: if target_deck_connected { 1.0 } else { 0.0 },
            target_deck_attenuated_fraction: attenuated_target_cells as f32
                / traced_cloud_cells.max(1) as f32,
            cloud_optical_depth: cloud_optical_depth.max(0.0),
            peak_blocking: clamp01(peak_blocking),
            clear_gap_count: clear_gap_count as f32,
            audit: path_audit_record(
                audit_trace.as_ref(),
                cloud_optical_depth - target_self_tau,
                target_self_transmission,
                peak_blocking,
                true,
                opaque_cutoff,
                attenuated_target_cells,
                target_cell,
                target_opacity,
            ),
        };
        if let Some(key) = shared_cloud_cache_key {
            if !shared_cloud_cache_loaded {
                buffers.store_solar_path_cloud_cache(key, self.solar_path_node_count());
            }
        }
        light
    }

    fn assess_observer_sky_v2(
        &self,
        sample_index: usize,
        out: &mut [f32],
        buffers: &mut SkyBuffers,
    ) {
        self.assess_observer_sky_v2_internal(sample_index, out, buffers, None, None);
    }

    fn assess_observer_sky_v2_with_audit(
        &self,
        sample_index: usize,
        out: &mut [f32],
        buffers: &mut SkyBuffers,
        audit: &mut PathAuditSummary,
    ) {
        self.assess_observer_sky_v2_internal(sample_index, out, buffers, Some(audit), None);
    }

    fn assess_observer_sky_v2_with_target_audit(
        &self,
        sample_index: usize,
        out: &mut [f32],
        buffers: &mut SkyBuffers,
        target_output: &mut [f32],
    ) {
        target_output.fill(0.0);
        self.assess_observer_sky_v2_internal(sample_index, out, buffers, None, Some(target_output));
    }

    fn assess_observer_sky_v2_internal(
        &self,
        sample_index: usize,
        out: &mut [f32],
        buffers: &mut SkyBuffers,
        mut audit: Option<&mut PathAuditSummary>,
        mut target_output: Option<&mut [f32]>,
    ) {
        out.fill(0.0);
        let sample =
            &self.samples[sample_index * SAMPLE_FIELDS..(sample_index + 1) * SAMPLE_FIELDS];
        if sample[SAMPLE_VALID] < 0.5 || self.rows == 0 || self.cols == 0 || self.time_count == 0 {
            return;
        }
        let diagnostics_enabled = audit.is_some() || target_output.is_some();
        buffers.reset(diagnostics_enabled);
        let observer_index = sample_index / self.step_count;
        let observer = &self.observers
            [observer_index * OBSERVER_FIELDS..(observer_index + 1) * OBSERVER_FIELDS];
        let obs_lat = observer[0];
        let obs_lon = observer[1];
        let finite = |value: f32| if value.is_finite() { value } else { 0.0 };
        let obs_elevation_m = finite(observer[2]);
        let solar_elevation = sample[0];
        let solar_azimuth = sample[1];
        let left = sample[2].max(0.0) as usize;
        let right = sample[3].max(0.0) as usize;
        let raw_weight = clamp01(sample[4]);
        let raw_left_seconds = sample[SAMPLE_LEFT_SECONDS].max(0.0);
        let raw_right_seconds = sample[SAMPLE_RIGHT_SECONDS].max(0.0);
        let key = temporal_cache_key(
            left,
            right,
            raw_weight,
            raw_left_seconds,
            raw_right_seconds,
            *TEMPORAL_CACHE_MODE,
        );
        let (weight, left_seconds, right_seconds) = temporal_cache_inputs(key);
        buffers.begin_layer_cache_for_key(self.cell_count, key);
        self.ensure_observer_geometry(buffers, observer_index, obs_lat, obs_lon);
        let az_rad = solar_azimuth.to_radians();
        let (az_sin, az_cos) = az_rad.sin_cos();
        let solar_illumination_elevation_rad =
            solar_illumination_elevation_deg(solar_elevation).to_radians();
        let observer_cell = buffers.observer_cell;

        let mut area_total = 0.0f32;
        let mut solar_ray_count = 0usize;
        let mut solar_rays_by_layer_count = [0usize; 3];
        let mut ground_view_sum = 0.0f32;
        let mut ground_burn_sum = 0.0f32;
        let mut ground_load_sum = 0.0f32;
        let mut local_layers = [CloudLayerSample::default(); 3];
        let mut local_burnable_fraction = [0.0f32; 3];
        let mut local_incoming_aerosol_illumination = [0.0f32; 3];
        let mut local_observer_aerosol_contrast = [0.0f32; 3];
        let mut local_observer_aerosol_optical_depth = [0.0f32; 3];
        let mut local_observer_aerosol_transmission = [0.0f32; 3];
        let mut nearest_distance = f32::INFINITY;
        let mut nearest_visibility = 10_000.0f32;
        let mut nearest_precipitation = 0.0f32;
        // The exact gate below permits the forward hemisphere and keeps a
        // one-degree boundary band for floating-point parity. Use the radius
        // bound to skip the opposite half of every observer row before
        // entering the per-cell scorer.
        let forward_boundary_margin_km = self.radius_km * 0.0174524;

        let mut pending_solar_cells = std::mem::take(&mut buffers.pending_solar_cells);
        macro_rules! process_pending_solar_cell {
            ($external_lights:expr, $pending:expr) => {{
                let external_lights = $external_lights;
                let pending = $pending;
                let geometry_index = pending.geometry_index;
                let geometry = buffers.observer_geometry[geometry_index];
                let cell = geometry.cell;
                let distance = geometry.distance_km;
                let observer_cell_sample = pending.observer_cell_sample;
                let target_lat = pending.work.target_lat;
                let target_distance = pending.work.target_distance_km;
                let target_bearing = pending.target_bearing;
                let layers = pending.work.clouds();
                let target_deep_risks = pending.work.target_deep_risk;
                let active_solar_layers = pending.work.active_layers();
                let mut burnable_fraction = [0.0f32; 3];
                let mut observer_burnable_fraction = [0.0f32; 3];
                let mut incoming_aerosol_illumination = [0.0f32; 3];
                let mut observer_aerosol_contrast = [0.0f32; 3];
                if buffers.solar_ray_diagnostics.is_some() {
                    let active_solar_layer_count =
                        active_solar_layers.iter().filter(|active| **active).count();
                    if active_solar_layer_count > 0 {
                        solar_ray_count += 1;
                        solar_rays_by_layer_count[active_solar_layer_count - 1] += 1;
                    }
                }
                let observer_traceable_layers = layers
                    .iter()
                    .filter(|cloud| {
                        cloud.amount >= 0.001
                            && cloud.top_m > obs_elevation_m
                            && cloud.base_m > obs_elevation_m
                    })
                    .count();
                let observer_paths = if let Some(cached) =
                    buffers.cached_observer_path_results(geometry_index)
                {
                    cached
                } else {
                    let computed =
                        if buffers.observer_path_batch_enabled && observer_traceable_layers > 1 {
                            self.observer_cloud_base_aerosol_optical_depths(
                                left,
                                right,
                                weight,
                                left_seconds,
                                right_seconds,
                                obs_lat,
                                obs_lon,
                                obs_elevation_m,
                                target_distance,
                                target_bearing,
                                layers,
                                observer_cell_sample,
                                buffers,
                            )
                        } else {
                            std::array::from_fn(|layer| {
                                let cloud = layers[layer];
                                if cloud.amount < 0.001 || cloud.top_m <= obs_elevation_m {
                                    return ObserverCloudBasePath::default();
                                }
                                self.observer_cloud_base_aerosol_optical_depth(
                                    left,
                                    right,
                                    weight,
                                    left_seconds,
                                    right_seconds,
                                    obs_lat,
                                    obs_lon,
                                    obs_elevation_m,
                                    target_distance,
                                    target_bearing,
                                    cloud.base_m,
                                    layer,
                                    observer_cell_sample,
                                    buffers,
                                )
                            })
                        };
                    buffers.store_observer_path_results(geometry_index, computed);
                    computed
                };
                for layer in 0..3 {
                    let cloud = layers[layer];
                    if cloud.amount < 0.001 || cloud.top_m <= obs_elevation_m {
                        continue;
                    }
                    let target_deep_risk = target_deep_risks[layer];
                    let external_light = if active_solar_layers[layer] {
                        external_lights[layer]
                    } else {
                        SolarCloudBaseLight::default()
                    };
                    let observer_path = observer_paths[layer];
                    let light = visible_cloud_base_light(
                        external_light,
                        observer_path.optical_depth,
                        observer_path.cloud_transmission,
                        cloud,
                        layer,
                        distance,
                        geometry.local_cell_radius_km,
                        solar_elevation,
                    );
                    burnable_fraction[layer] = light.burnable_fraction;
                    observer_burnable_fraction[layer] = light.observer_burnable_fraction;
                    incoming_aerosol_illumination[layer] = light.incoming_aerosol_illumination;
                    observer_aerosol_contrast[layer] = light.observer_aerosol_contrast;
                    if observer_cell_sample {
                        local_observer_aerosol_optical_depth[layer] =
                            light.observer_aerosol_optical_depth;
                        local_observer_aerosol_transmission[layer] =
                            light.observer_aerosol_transmission;
                    }
                    let target_terrain_asl_m = if observer_cell_sample {
                        obs_elevation_m
                    } else {
                        finite(self.cached_terrain_elevation(buffers, left, right, weight, cell))
                    };
                    let target_eligible = if layer == LOW {
                        low_cloud_target_is_eligible(
                            external_light,
                            observer_path,
                            cloud,
                            obs_elevation_m,
                            target_distance,
                        )
                    } else {
                        !observer_path.terrain_blocked && cloud.base_m > obs_elevation_m
                    };
                    if layer == LOW && (!target_eligible || !external_light.geometry_reachable) {
                        // A low cloud may still block the incoming path and
                        // therefore remains in the projection/ledger. Its
                        // own burn contribution is independently ineligible.
                        burnable_fraction[layer] = 0.0;
                        observer_burnable_fraction[layer] = 0.0;
                        incoming_aerosol_illumination[layer] = 0.0;
                        observer_aerosol_contrast[layer] = 0.0;
                    }
                    let geometry_reachable = target_eligible && external_light.geometry_reachable;
                    let external_path_transmission = if geometry_reachable {
                        clamp01(external_light.path_cloud_transmission_before_target_self)
                    } else {
                        0.0
                    };
                    let projection_base_m = (cloud.base_m - obs_elevation_m).max(0.0);
                    let projection_top_m = (cloud.top_m - obs_elevation_m).max(0.0);
                    let projection = CloudProjection {
                        audit_slot: buffers.ledger_projections.len(),
                        angular_footprint: angular_cloud_footprint_with_radius(
                            target_bearing,
                            solar_azimuth,
                            target_distance,
                            projection_base_m,
                            projection_top_m,
                            self.spacing,
                            target_lat,
                            geometry.local_cell_radius_km,
                        ),
                        target_cell_index: cell,
                        distance_km: target_distance,
                        bearing_deg: target_bearing,
                        latitude_deg: target_lat,
                        base_m: projection_base_m,
                        amount: cloud.amount,
                        target_opacity: cloud.opacity,
                        material_quality: cloud.material_quality,
                        burnable_fraction: observer_burnable_fraction[layer],
                        incoming_aerosol_illumination: incoming_aerosol_illumination[layer],
                        observer_aerosol_contrast: observer_aerosol_contrast[layer],
                        observer_aerosol_optical_depth: light.observer_aerosol_optical_depth,
                        observer_aerosol_transmission: light.observer_aerosol_transmission,
                        target_deck_connected: external_light.target_deck_connected,
                        target_deck_attenuated_fraction: external_light
                            .target_deck_attenuated_fraction,
                        target_deep_risk,
                        cloud_optical_depth: external_light.cloud_optical_depth,
                        peak_blocking: external_light.peak_blocking,
                        clear_gap_count: external_light.clear_gap_count,
                        target_base_asl_m: cloud.base_m,
                        target_top_asl_m: cloud.top_m,
                        target_terrain_asl_m,
                        layer,
                        target_eligible,
                        geometry_reachable,
                        external_path_transmission,
                        observer_path_blocked: observer_path.terrain_blocked,
                    };
                    // Keep a pre-observer-terrain copy for the candidate ledger.
                    // It is never read by the score path.
                    buffers.ledger_projections.push(projection);
                    if observer_path.terrain_blocked {
                        // A mountain blocks the cloud-to-observer leg. The
                        // ground-area sidecar still records the cloud's raw
                        // sunlit area, but the observer-facing projection and
                        // main score must not see through the ridge.
                        continue;
                    }
                    if buffers.audit_enabled || !buffers.unified_projection_sort_enabled {
                        buffers.projections.push(projection);
                        if buffers.audit_enabled {
                            buffers.audit_records.push(external_light.audit);
                        }
                    }
                }
                // Ground-area diagnostics use the same low-before-mid-before-high
                // visibility order as the angular sky compositor.
                let mut remaining = 1.0f32;
                let mut column_view = 0.0f32;
                let mut column_burn = 0.0f32;
                let mut column_load = 0.0f32;
                for layer in [2usize, 1, 0] {
                    let visible = remaining * layers[layer].amount;
                    let burn = visible * burnable_fraction[layer];
                    column_view += visible;
                    column_burn += burn;
                    column_load += burn
                        * layers[layer].material_quality
                        * incoming_aerosol_illumination[layer]
                        * observer_aerosol_contrast[layer];
                    remaining *= 1.0 - layers[layer].amount;
                }
                ground_view_sum += geometry.area_weight * clamp01(column_view);
                ground_burn_sum += geometry.area_weight * clamp01(column_burn);
                ground_load_sum += geometry.area_weight * (2.5 * column_load).min(2.5);
                if cell == observer_cell {
                    local_layers = layers;
                    local_burnable_fraction = observer_burnable_fraction;
                    local_incoming_aerosol_illumination = incoming_aerosol_illumination;
                    local_observer_aerosol_contrast = observer_aerosol_contrast;
                }
            }};
        }

        let observer_geometry_row_count = buffers.observer_geometry_rows.len();
        for row_index in 0..observer_geometry_row_count {
            let row_geometry = buffers.observer_geometry_rows[row_index];
            if row_geometry.start >= row_geometry.end {
                continue;
            }
            let first_geometry = buffers.observer_geometry[row_geometry.start];
            let east_scale = 111.320 * ((obs_lat + first_geometry.lat) * 0.5).to_radians().cos();
            let (candidate_col_min, candidate_col_max) = if east_scale.abs() <= 1e-6 {
                if first_geometry.north_km * az_cos < -forward_boundary_margin_km {
                    continue;
                }
                (row_geometry.first_col, self.cols.saturating_sub(1))
            } else if az_sin.abs() <= 1e-5 {
                if first_geometry.north_km * az_cos < -forward_boundary_margin_km {
                    continue;
                }
                (row_geometry.first_col, self.cols.saturating_sub(1))
            } else {
                let east_threshold_km =
                    (-forward_boundary_margin_km - first_geometry.north_km * az_cos) / az_sin;
                let longitude_threshold = obs_lon + east_threshold_km / east_scale;
                let threshold_index = ((longitude_threshold - self.lon0) / self.spacing)
                    .floor()
                    .clamp(-1_000_000.0, 1_000_000.0)
                    as isize;
                if az_sin > 0.0 {
                    (
                        threshold_index.saturating_sub(1).max(0) as usize,
                        self.cols.saturating_sub(1),
                    )
                } else {
                    (
                        row_geometry.first_col,
                        (threshold_index.saturating_add(2).max(0) as usize)
                            .min(self.cols.saturating_sub(1)),
                    )
                }
            };
            let row_col_max = row_geometry.first_col
                + row_geometry
                    .end
                    .saturating_sub(row_geometry.start)
                    .saturating_sub(1);
            let col_min = candidate_col_min.max(row_geometry.first_col);
            let col_max = candidate_col_max.min(row_col_max);
            if col_min > col_max {
                continue;
            }
            let geometry_start = row_geometry.start + col_min - row_geometry.first_col;
            let geometry_end = row_geometry.start + col_max - row_geometry.first_col + 1;
            for geometry_index in geometry_start..geometry_end {
                let geometry = buffers.observer_geometry[geometry_index];
                let cell = geometry.cell;
                let lat = geometry.lat;
                let north_km = geometry.north_km;
                let east_km = geometry.east_km;
                let distance = geometry.distance_km;
                // The observer field accepts the solar-facing half-plane.
                // Use the same local-vector dot product as the angular gate;
                // the exact bearing is only needed after a cloud survives.
                let forward_dot = east_km * az_sin + north_km * az_cos;
                if cell != observer_cell && forward_dot < 0.0 {
                    let distance_sq = east_km * east_km + north_km * north_km;
                    // Cells safely outside the forward hemisphere are rejected
                    // without atan2. Keep the exact sector calculation only in
                    // the one-degree floating-point boundary band used by the
                    // old path, preserving its bit-level inclusion semantics.
                    if forward_dot * forward_dot > 0.000304 * distance_sq {
                        continue;
                    }
                    let sector =
                        signed_angular_difference(geometry.bearing_deg, solar_azimuth).abs();
                    if sector > FIELD_FORWARD_HALF_DEG {
                        continue;
                    }
                }
                area_total += geometry.area_weight;
                if distance < nearest_distance {
                    nearest_distance = distance;
                    let visibility =
                        finite(self.interpolated(left, right, weight, cell, VISIBILITY));
                    if visibility > 0.0 {
                        nearest_visibility = visibility;
                    }
                    nearest_precipitation =
                        finite(self.interpolated(left, right, weight, cell, PRECIPITATION));
                }

                let observer_cell_sample = cell == observer_cell;
                let target_lat = if observer_cell_sample { obs_lat } else { lat };
                let target_lon = if observer_cell_sample {
                    obs_lon
                } else {
                    geometry.lon
                };
                let target_distance = if observer_cell_sample { 0.0 } else { distance };
                buffers.begin_observer_geometry_path(geometry_index);
                let sampled_layers = if observer_cell_sample {
                    self.cloud_horizontal_weights(target_lat, target_lon)
                        .map(|horizontal| {
                            self.cached_motion_cloud_layers_with_weights(
                                buffers,
                                left,
                                right,
                                weight,
                                left_seconds,
                                right_seconds,
                                &horizontal,
                            )
                        })
                        .unwrap_or([CloudLayerSample::default(); 3])
                } else {
                    self.cached_motion_cloud_layers_for_cell(
                        buffers,
                        left,
                        right,
                        weight,
                        left_seconds,
                        right_seconds,
                        cell,
                    )
                };
                let mut layers = [CloudLayerSample::default(); 3];
                let mut target_deep_risks = [0.0f32; 3];
                for layer in 0..3 {
                    let mut cloud = sampled_layers[layer];
                    if observer_cell_sample && layer == 0 {
                        let local_upper_support = self.cached_motion_family_scalar(
                            buffers,
                            left,
                            right,
                            weight,
                            left_seconds,
                            right_seconds,
                            target_lat,
                            target_lon,
                            0,
                            LOCAL_UPPER_SUPPORT,
                        );
                        cloud.amount = cloud.amount.max(clamp01(finite(local_upper_support)));
                    }
                    layers[layer] = cloud;
                    if cloud.amount < 0.001 || cloud.top_m <= obs_elevation_m {
                        continue;
                    }
                    let target_body_risk_field = match layer {
                        HIGH => HIGH_TARGET_BODY_RISK,
                        MID => MID_TARGET_BODY_RISK,
                        _ => LOW_TARGET_BODY_RISK,
                    };
                    let target_deep_risk = if observer_cell_sample {
                        self.cached_motion_family_scalar(
                            buffers,
                            left,
                            right,
                            weight,
                            left_seconds,
                            right_seconds,
                            target_lat,
                            target_lon,
                            layer,
                            target_body_risk_field,
                        )
                    } else {
                        self.cached_motion_family_scalar_for_exact_cell(
                            buffers,
                            left,
                            right,
                            weight,
                            left_seconds,
                            right_seconds,
                            target_lat,
                            target_lon,
                            cell,
                            layer,
                            target_body_risk_field,
                        )
                        .unwrap_or_else(|| {
                            self.cached_motion_family_scalar(
                                buffers,
                                left,
                                right,
                                weight,
                                left_seconds,
                                right_seconds,
                                target_lat,
                                target_lon,
                                layer,
                                target_body_risk_field,
                            )
                        })
                    };
                    target_deep_risks[layer] = clamp01(finite(target_deep_risk));
                }
                let active_solar_layers = std::array::from_fn(|layer| {
                    let cloud = layers[layer];
                    cloud.amount >= 0.001
                        && cloud.top_m > obs_elevation_m
                        && cloud.base_m > obs_elevation_m
                });
                let has_observable_cloud = layers
                    .iter()
                    .any(|cloud| cloud.amount >= 0.001 && cloud.top_m > obs_elevation_m);
                let (target_bearing, target_sector) = if observer_cell_sample {
                    (solar_azimuth, 0.0)
                } else if has_observable_cloud {
                    let bearing = geometry.bearing_deg;
                    (
                        bearing,
                        signed_angular_difference(bearing, solar_azimuth).abs(),
                    )
                } else {
                    (0.0, 0.0)
                };
                let target_sector_cos = target_sector.to_radians().cos();
                // One merged three-layer work per target cell; the scalar
                // evaluator treats each active bit independently.
                let work = SolarRayWork::from_solar_inputs(
                    left,
                    right,
                    weight,
                    left_seconds,
                    right_seconds,
                    target_lat,
                    target_lon,
                    target_distance,
                    target_sector,
                    target_sector_cos,
                    active_solar_layers,
                    layers,
                    target_deep_risks,
                    solar_elevation,
                    solar_illumination_elevation_rad,
                    solar_azimuth,
                    if observer_cell_sample {
                        Some(obs_elevation_m)
                    } else {
                        None
                    },
                );
                pending_solar_cells.push(PendingSolarCell {
                    geometry_index,
                    observer_cell_sample,
                    target_bearing,
                    work,
                });
                continue;
            }
        }
        for pending in pending_solar_cells.iter() {
            let geometry_index = pending.geometry_index;
            buffers.begin_observer_geometry_path(geometry_index);
            let external_lights = if pending.work.active_mask != 0 {
                pending.work.evaluate_scalar(self, buffers)
            } else {
                [SolarCloudBaseLight::default(); 3]
            };
            process_pending_solar_cell!(external_lights, *pending);
        }
        buffers.pending_solar_cells = pending_solar_cells;
        if let Some(diagnostics) = &buffers.solar_ray_diagnostics {
            diagnostics.record(solar_ray_count, solar_rays_by_layer_count);
        }
        if area_total <= 0.0 {
            return;
        }

        // `audit_slot` is assigned in the original projection-generation
        // order and is shared by the ledger and observer-facing vectors.
        // Create the accumulators before either vector is sorted; otherwise
        // sorted ledger order pairs one target's contributions with another
        // target's metadata. This is diagnostic-only and does not affect the
        // authoritative score buffers.
        let mut target_accumulators: Vec<TargetAuditAccumulator> = if target_output.is_some() {
            buffers
                .ledger_projections
                .iter()
                .copied()
                .map(TargetAuditAccumulator::new)
                .collect()
        } else {
            Vec::new()
        };

        // A line of sight meets the nearest cloud surface first. For equal
        // distance cells, low cloud is composited before middle and high cloud.
        // Audit records describe the same projection and must remain paired
        // while this order changes. The normal scoring path keeps its original
        // projection-only sort and does not allocate the audit pairs.
        let use_fused_unified_projection = buffers.unified_projection_sort_enabled
            && !buffers.audit_enabled
            && !diagnostics_enabled;
        if buffers.audit_enabled {
            debug_assert_eq!(buffers.projections.len(), buffers.audit_records.len());
            let mut paired: Vec<(CloudProjection, PathAuditRecord)> = buffers
                .projections
                .drain(..)
                .zip(buffers.audit_records.drain(..))
                .collect();
            paired.sort_unstable_by(|(left_cloud, _), (right_cloud, _)| {
                compare_cloud_projection_order(left_cloud, right_cloud)
            });
            for (projection, record) in paired {
                buffers.projections.push(projection);
                buffers.audit_records.push(record);
            }
        } else if !buffers.unified_projection_sort_enabled {
            buffers
                .projections
                .sort_unstable_by(compare_cloud_projection_order);
        }
        buffers
            .ledger_projections
            .sort_unstable_by(compare_cloud_projection_order);

        let mut family_burn_solid_angle = [0.0f32; 3];
        let mut family_base_asl_weighted_sum = [0.0f32; 3];
        let mut family_top_asl_weighted_sum = [0.0f32; 3];
        let mut family_terrain_asl_weighted_sum = [0.0f32; 3];
        let elevation_solid_angle_weights = &*VIEW_ELEVATION_SOLID_ANGLE_WEIGHTS;
        if use_fused_unified_projection {
            for projection_index in 0..buffers.ledger_projections.len() {
                let cloud = buffers.ledger_projections[projection_index];
                let observer_visible = !cloud.observer_path_blocked;
                for_each_projection_angular_cloud_cell(
                    cloud,
                    solar_azimuth,
                    self.spacing,
                    buffers.angular_footprint_cache_enabled,
                    |index, overlap| {
                        let candidate = clamp01(cloud.amount * overlap);
                        let ledger_family_index = cloud.layer * VIEW_ANGULAR_CELLS + index;
                        let previous_ledger_family =
                            buffers.ledger_layer_occupied[ledger_family_index];
                        if candidate > previous_ledger_family + 1e-6 {
                            let conditional = clamp01(
                                (candidate - previous_ledger_family)
                                    / (1.0 - previous_ledger_family).max(1e-6),
                            );
                            buffers.ledger_layer_occupied[ledger_family_index] = candidate;
                            let visible = (1.0 - buffers.ledger_view[index]) * conditional;
                            if let Some(record) = target_accumulators.get_mut(cloud.audit_slot) {
                                let angular_weight =
                                    VIEW_ELEVATION_SOLID_ANGLE_WEIGHTS[index % VIEW_ELEVATION_BINS];
                                record.candidate_overlap_solid_angle += visible * angular_weight;
                            }
                            if visible > 0.0 {
                                buffers.ledger_view[index] += visible;
                            }
                        }
                        if !observer_visible {
                            return;
                        }
                        let unoccluded_burn = candidate * clamp01(cloud.burnable_fraction);
                        buffers.unoccluded_burn_upper[index] =
                            (buffers.unoccluded_burn_upper[index] + unoccluded_burn).min(1.0);
                        let family_index = cloud.layer * VIEW_ANGULAR_CELLS + index;
                        let previous_family = buffers.layer_occupied[family_index];
                        if candidate <= previous_family + 1e-6 {
                            return;
                        }
                        let conditional = clamp01(
                            (candidate - previous_family) / (1.0 - previous_family).max(1e-6),
                        );
                        buffers.layer_occupied[family_index] = candidate;
                        let visible = (1.0 - buffers.sky_view[index]) * conditional;
                        if visible <= 0.0 {
                            return;
                        }
                        let burn = visible * cloud.burnable_fraction;
                        buffers.ledger_target_eligible[index] +=
                            visible * if cloud.target_eligible { 1.0 } else { 0.0 };
                        buffers.ledger_geometry_reachable[index] +=
                            visible * if cloud.geometry_reachable { 1.0 } else { 0.0 };
                        buffers.ledger_external_path_transmission[index] +=
                            visible * cloud.external_path_transmission;
                        buffers.sky_view[index] += visible;
                        buffers.sky_burn[index] += burn;
                        buffers.sky_material_load[index] += burn * cloud.material_quality;
                        buffers.sky_incoming_aerosol_load[index] +=
                            burn * cloud.material_quality * cloud.incoming_aerosol_illumination;
                        buffers.observer_aerosol_optical_depth[index] +=
                            visible * cloud.observer_aerosol_optical_depth;
                        buffers.observer_aerosol_transmission[index] +=
                            visible * cloud.observer_aerosol_transmission;
                        buffers.sky_load[index] += burn
                            * cloud.material_quality
                            * cloud.incoming_aerosol_illumination
                            * cloud.observer_aerosol_contrast;
                        buffers.layer_view[cloud.layer][index] += visible;
                        buffers.layer_burn[cloud.layer][index] += burn;
                        buffers.incoming_blocked[index] +=
                            visible * (1.0 - cloud.burnable_fraction);
                        buffers.target_deck_connected[index] +=
                            visible * cloud.target_deck_connected;
                        buffers.target_deck_attenuated[index] +=
                            visible * cloud.target_deck_attenuated_fraction;
                        buffers.target_deep_risk[index] += visible * cloud.target_deep_risk;
                        buffers.cloud_optical_depth[index] += visible * cloud.cloud_optical_depth;
                        buffers.peak_blocking[index] += visible * cloud.peak_blocking;
                        buffers.clear_gap_count[index] += visible * cloud.clear_gap_count;
                        let elevation_bin = index % VIEW_ELEVATION_BINS;
                        let diagnostic_weight = burn * elevation_solid_angle_weights[elevation_bin];
                        family_burn_solid_angle[cloud.layer] += diagnostic_weight;
                        family_base_asl_weighted_sum[cloud.layer] +=
                            diagnostic_weight * cloud.target_base_asl_m;
                        family_top_asl_weighted_sum[cloud.layer] +=
                            diagnostic_weight * cloud.target_top_asl_m;
                        family_terrain_asl_weighted_sum[cloud.layer] +=
                            diagnostic_weight * cloud.target_terrain_asl_m;
                    },
                );
            }
        } else {
            // Candidate coverage uses the same angular compositor as the
            // authoritative view, but retains projections that are later removed
            // by the observer-terrain veto. This makes candidate - view a
            // non-scoring terrain/eligibility ledger rather than a second score.
            for projection_index in 0..buffers.ledger_projections.len() {
                let cloud = buffers.ledger_projections[projection_index];
                for_each_projection_angular_cloud_cell(
                    cloud,
                    solar_azimuth,
                    self.spacing,
                    buffers.angular_footprint_cache_enabled,
                    |index, overlap| {
                        let candidate = clamp01(cloud.amount * overlap);
                        let family_index = cloud.layer * VIEW_ANGULAR_CELLS + index;
                        let previous_family = buffers.ledger_layer_occupied[family_index];
                        if candidate <= previous_family + 1e-6 {
                            return;
                        }
                        let conditional = clamp01(
                            (candidate - previous_family) / (1.0 - previous_family).max(1e-6),
                        );
                        buffers.ledger_layer_occupied[family_index] = candidate;
                        let visible = (1.0 - buffers.ledger_view[index]) * conditional;
                        if let Some(record) = target_accumulators.get_mut(cloud.audit_slot) {
                            let angular_weight =
                                VIEW_ELEVATION_SOLID_ANGLE_WEIGHTS[index % VIEW_ELEVATION_BINS];
                            record.candidate_overlap_solid_angle += visible * angular_weight;
                        }
                        if visible > 0.0 {
                            buffers.ledger_view[index] += visible;
                        }
                    },
                );
            }

            let score_projection_count =
                if buffers.audit_enabled || !buffers.unified_projection_sort_enabled {
                    buffers.projections.len()
                } else {
                    buffers.ledger_projections.len()
                };
            for projection_index in 0..score_projection_count {
                let cloud = if buffers.audit_enabled || !buffers.unified_projection_sort_enabled {
                    buffers.projections[projection_index]
                } else {
                    buffers.ledger_projections[projection_index]
                };
                if buffers.unified_projection_sort_enabled && cloud.observer_path_blocked {
                    continue;
                }
                let path_audit = if diagnostics_enabled {
                    buffers.audit_records.get(projection_index).copied()
                } else {
                    None
                };
                let mut path_visible_weight = 0.0f32;
                let mut projection_self_loss_solid_angle = 0.0f32;
                let mut path_external_tau = 0.0f32;
                let mut path_target_self_tau = 0.0f32;
                let mut path_pre_peak_external_transmission = 0.0f32;
                let mut path_peak_factor = 0.0f32;
                for_each_projection_angular_cloud_cell(
                    cloud,
                    solar_azimuth,
                    self.spacing,
                    buffers.angular_footprint_cache_enabled,
                    |index, overlap| {
                        let candidate = clamp01(cloud.amount * overlap);
                        let unoccluded_burn = candidate * clamp01(cloud.burnable_fraction);
                        buffers.unoccluded_burn_upper[index] =
                            (buffers.unoccluded_burn_upper[index] + unoccluded_burn).min(1.0);
                        let family_index = cloud.layer * VIEW_ANGULAR_CELLS + index;
                        let previous_family = buffers.layer_occupied[family_index];
                        if candidate <= previous_family + 1e-6 {
                            return;
                        }
                        // Convert the increase from the previous family maximum to
                        // a conditional fraction of the still-open same-family sky.
                        // This keeps a uniform 80% deck at 80%, while allowing a
                        // farther 100% patch to fill the remaining 20%.
                        let conditional = clamp01(
                            (candidate - previous_family) / (1.0 - previous_family).max(1e-6),
                        );
                        buffers.layer_occupied[family_index] = candidate;
                        let visible = (1.0 - buffers.sky_view[index]) * conditional;
                        if visible <= 0.0 {
                            return;
                        }
                        let burn = visible * cloud.burnable_fraction;
                        if let Some(record) = target_accumulators.get_mut(cloud.audit_slot) {
                            let angular_weight =
                                elevation_solid_angle_weights[index % VIEW_ELEVATION_BINS];
                            record.visible_overlap_solid_angle += visible * angular_weight;
                            record.burn_contribution_solid_angle += burn * angular_weight;
                        }
                        let target_eligible = if cloud.target_eligible { 1.0 } else { 0.0 };
                        let geometry_reachable = if cloud.geometry_reachable { 1.0 } else { 0.0 };
                        buffers.ledger_target_eligible[index] += visible * target_eligible;
                        buffers.ledger_geometry_reachable[index] += visible * geometry_reachable;
                        buffers.ledger_external_path_transmission[index] +=
                            visible * cloud.external_path_transmission;
                        buffers.sky_view[index] += visible;
                        buffers.sky_burn[index] += burn;
                        buffers.sky_material_load[index] += burn * cloud.material_quality;
                        buffers.sky_incoming_aerosol_load[index] +=
                            burn * cloud.material_quality * cloud.incoming_aerosol_illumination;
                        buffers.observer_aerosol_optical_depth[index] +=
                            visible * cloud.observer_aerosol_optical_depth;
                        buffers.observer_aerosol_transmission[index] +=
                            visible * cloud.observer_aerosol_transmission;
                        buffers.sky_load[index] += burn
                            * cloud.material_quality
                            * cloud.incoming_aerosol_illumination
                            * cloud.observer_aerosol_contrast;
                        buffers.layer_view[cloud.layer][index] += visible;
                        buffers.layer_burn[cloud.layer][index] += burn;
                        buffers.incoming_blocked[index] +=
                            visible * (1.0 - cloud.burnable_fraction);
                        buffers.target_deck_connected[index] +=
                            visible * cloud.target_deck_connected;
                        buffers.target_deck_attenuated[index] +=
                            visible * cloud.target_deck_attenuated_fraction;
                        buffers.target_deep_risk[index] += visible * cloud.target_deep_risk;
                        buffers.cloud_optical_depth[index] += visible * cloud.cloud_optical_depth;
                        buffers.peak_blocking[index] += visible * cloud.peak_blocking;
                        buffers.clear_gap_count[index] += visible * cloud.clear_gap_count;
                        let elevation_bin = index % VIEW_ELEVATION_BINS;
                        if path_audit.is_some() {
                            let angular_weight = elevation_solid_angle_weights[elevation_bin];
                            let weighted_visible = visible * angular_weight;
                            path_visible_weight += weighted_visible;
                            projection_self_loss_solid_angle += weighted_visible
                                * (cloud.external_path_transmission - cloud.burnable_fraction)
                                    .max(0.0);
                            let path = path_audit.expect("audit path record is present");
                            if let Some(record) = target_accumulators.get_mut(cloud.audit_slot) {
                                record.external_tau = path.external_tau;
                                record.target_self_tau = path.target_self_tau;
                                record.path_audit_available = true;
                            }
                            path_external_tau += weighted_visible * path.external_tau;
                            path_target_self_tau += weighted_visible * path.target_self_tau;
                            // Q is the pre-peak transmission for the same
                            // geometry-reachable target set as G. A solar path
                            // can be traceable for an ineligible target (for
                            // example nearby low cloud); do not let that path
                            // enter the field-level Q ledger.
                            if cloud.geometry_reachable {
                                path_pre_peak_external_transmission +=
                                    weighted_visible * path.pre_peak_external_transmission;
                            }
                            path_peak_factor += weighted_visible * path.peak_factor;
                        }
                        let diagnostic_weight = burn * elevation_solid_angle_weights[elevation_bin];
                        family_burn_solid_angle[cloud.layer] += diagnostic_weight;
                        family_base_asl_weighted_sum[cloud.layer] +=
                            diagnostic_weight * cloud.target_base_asl_m;
                        family_top_asl_weighted_sum[cloud.layer] +=
                            diagnostic_weight * cloud.target_top_asl_m;
                        family_terrain_asl_weighted_sum[cloud.layer] +=
                            diagnostic_weight * cloud.target_terrain_asl_m;
                    },
                );
                if let Some(path) = path_audit {
                    if path_visible_weight > 1e-6 {
                        if let Some(summary) = audit.as_deref_mut() {
                            summary.total_self_loss_solid_angle += projection_self_loss_solid_angle;
                            if projection_self_loss_solid_angle
                                > summary.dominant_self_loss_solid_angle
                            {
                                summary.dominant_self_loss_solid_angle =
                                    projection_self_loss_solid_angle;
                                summary.dominant_projection = Some(cloud);
                                summary.dominant_audit_record = Some(path);
                            }
                            summary.visible_weight += path_visible_weight;
                            summary.external_tau_weighted_sum += path_external_tau;
                            summary.target_self_tau_weighted_sum += path_target_self_tau;
                            summary.pre_peak_external_transmission_weighted_sum +=
                                path_pre_peak_external_transmission;
                            summary.peak_factor_weighted_sum += path_peak_factor;
                            summary.trace_steps_max = summary.trace_steps_max.max(path.trace_steps);
                            summary.unique_cells_max =
                                summary.unique_cells_max.max(path.unique_cells);
                            summary.revisited_cells_max =
                                summary.revisited_cells_max.max(path.revisited_cells);
                            summary.domain_break_count += path.domain_break as u32;
                            summary.ray_height_break_count += path.ray_height_break as u32;
                            summary.opaque_cutoff_count += path.opaque_cutoff as u32;
                            summary.family_tau_weighted_sum[cloud.layer] += path_external_tau;
                            summary.target_extension_count_max = summary
                                .target_extension_count_max
                                .max(path.target_extension_count_max);
                        }
                    }
                }
            }
        }

        let angular = reduce_angular_fields(buffers);
        let candidate_field = angular_solid_angle_field(angular.candidate_solid_angle);
        let target_eligible_field = angular_solid_angle_field(angular.target_eligible_solid_angle);
        let geometry_reachable_field =
            angular_solid_angle_field(angular.geometry_reachable_solid_angle);
        let external_path_transmission_field =
            angular_solid_angle_field(angular.external_path_transmission_solid_angle);
        let unoccluded_burn_upper =
            angular_solid_angle_field(angular.unoccluded_burn_upper_solid_angle);
        let field_view = angular_solid_angle_field(angular.field_view_solid_angle);
        let field_burn = angular_solid_angle_field(angular.field_burn_solid_angle);
        if let Some(summary) = audit.as_deref_mut() {
            let pb_loss_total = (external_path_transmission_field - field_burn).max(0.0);
            let dominant_loss_field =
                angular_solid_angle_field(summary.dominant_self_loss_solid_angle);
            let total_loss_field = angular_solid_angle_field(summary.total_self_loss_solid_angle);
            let omitted_loss_field = (total_loss_field - dominant_loss_field).max(0.0);
            summary.pb_loss_total = pb_loss_total;
            summary.pb_decomposition_error =
                pb_loss_total - dominant_loss_field - omitted_loss_field;
        }
        let field_load = (2.5 * angular_solid_angle_field(angular.field_load_solid_angle)).min(2.5);
        let field_material_load =
            (2.5 * angular_solid_angle_field(angular.field_material_load_solid_angle)).min(2.5);
        let field_incoming_aerosol_load = (2.5
            * angular_solid_angle_field(angular.field_incoming_aerosol_load_solid_angle))
        .min(2.5);
        let field_observer_aerosol_optical_depth = if angular.weighted_visible > 1e-6 {
            angular.observer_aerosol_optical_depth_weighted_sum / angular.weighted_visible
        } else {
            0.0
        };
        let field_observer_aerosol_transmission = if angular.weighted_visible > 1e-6 {
            angular.observer_aerosol_transmission_weighted_sum / angular.weighted_visible
        } else {
            0.0
        };
        let high_view = angular_solid_angle_field(angular.high_view_solid_angle);
        let mid_view = angular_solid_angle_field(angular.mid_view_solid_angle);
        let low_view = angular_solid_angle_field(angular.low_view_solid_angle);
        let high_burn = angular_solid_angle_field(angular.high_burn_solid_angle);
        let mid_burn = angular_solid_angle_field(angular.mid_burn_solid_angle);
        let low_burn = angular_solid_angle_field(angular.low_burn_solid_angle);
        let incoming_blocked_amount =
            angular_solid_angle_field(angular.incoming_blocked_solid_angle);
        let incoming_block_risk = if field_view > 1e-6 {
            clamp01(incoming_blocked_amount / field_view)
        } else {
            0.0
        };
        let target_deck_connected = if angular.weighted_visible > 1e-6 {
            clamp01(angular.target_deck_connected_weighted_sum / angular.weighted_visible)
        } else {
            0.0
        };
        let target_deck_attenuated = if angular.weighted_visible > 1e-6 {
            clamp01(angular.target_deck_attenuated_weighted_sum / angular.weighted_visible)
        } else {
            0.0
        };
        let target_deep_risk = if angular.weighted_visible > 1e-6 {
            clamp01(angular.target_deep_risk_weighted_sum / angular.weighted_visible)
        } else {
            0.0
        };
        let cloud_optical_depth = if angular.weighted_visible > 1e-6 {
            angular.cloud_optical_depth_weighted_sum / angular.weighted_visible
        } else {
            0.0
        };
        let peak_blocking = if angular.weighted_visible > 1e-6 {
            clamp01(angular.peak_blocking_weighted_sum / angular.weighted_visible)
        } else {
            0.0
        };
        let clear_gap_count = if angular.weighted_visible > 1e-6 {
            angular.clear_gap_count_weighted_sum / angular.weighted_visible
        } else {
            0.0
        };
        let family_burn = [high_burn, mid_burn, low_burn];
        let dominant_family = dominant_burnable_pressure_family_index(family_burn);
        let dominant_family_index = dominant_family.unwrap_or(0);
        let dominant_family_code = dominant_family.map_or(0.0, |index| (index + 1) as f32);
        let dominant_weight = dominant_family
            .map(|index| family_burn_solid_angle[index])
            .unwrap_or(0.0);
        let dominant_base_asl_mean_m = conditional_weighted_mean(
            family_base_asl_weighted_sum[dominant_family_index],
            dominant_weight,
        );
        let dominant_top_asl_mean_m = conditional_weighted_mean(
            family_top_asl_weighted_sum[dominant_family_index],
            dominant_weight,
        );
        let dominant_target_terrain_asl_mean_m = conditional_weighted_mean(
            family_terrain_asl_weighted_sum[dominant_family_index],
            dominant_weight,
        );
        let ratio = if field_view > 1e-6 {
            clamp01(field_burn / field_view)
        } else {
            0.0
        };
        let mut local_remaining = 1.0f32;
        let mut local_view = 0.0f32;
        let mut local_burn = 0.0f32;
        let mut local_load = 0.0f32;
        let mut local_material_load = 0.0f32;
        let mut local_incoming_aerosol_load = 0.0f32;
        let mut local_return_aerosol_optical_depth = 0.0f32;
        let mut local_return_aerosol_transmission = 0.0f32;
        for layer in [2usize, 1, 0] {
            let visible = local_remaining * local_layers[layer].amount;
            let burn = visible * local_burnable_fraction[layer];
            local_view += visible;
            local_burn += burn;
            local_material_load += burn * local_layers[layer].material_quality;
            local_incoming_aerosol_load += burn
                * local_layers[layer].material_quality
                * local_incoming_aerosol_illumination[layer];
            local_return_aerosol_optical_depth +=
                visible * local_observer_aerosol_optical_depth[layer];
            local_return_aerosol_transmission +=
                visible * local_observer_aerosol_transmission[layer];
            local_load += burn
                * local_layers[layer].material_quality
                * local_incoming_aerosol_illumination[layer]
                * local_observer_aerosol_contrast[layer];
            local_remaining *= 1.0 - local_layers[layer].amount;
        }
        // Keep local return diagnostics on the same conditional-mean basis as
        // the full angular field. A visible cloud fraction of 0.1 must not
        // make the same optical depth look ten times smaller than a full
        // column; clear local sky is represented by local_view == 0.
        let local_return_aerosol_optical_depth =
            conditional_weighted_mean(local_return_aerosol_optical_depth, local_view);
        let local_return_aerosol_transmission =
            conditional_weighted_mean(local_return_aerosol_transmission, local_view);
        let ground_view = clamp01(ground_view_sum / area_total);
        let ground_burn = clamp01(ground_burn_sum / area_total);
        let ground_load = (ground_load_sum / area_total).clamp(0.0, 2.5);
        let visibility_gate = if nearest_visibility > 0.0 {
            smooth_gate(nearest_visibility, 1500.0, 8000.0)
        } else {
            1.0
        };
        let precipitation_mm_h = nearest_precipitation.max(0.0) * 3600.0;
        let precipitation_gate = 1.0 - smooth_gate(precipitation_mm_h, 0.05, 1.0);
        let surface_weather_gate = clamp01(visibility_gate.min(precipitation_gate));
        let vividness = clamp01(0.45 + 0.30 * ratio + 0.25 * (0.65 * high_burn + 0.35 * mid_burn));

        out[0] = 1.0;
        out[1] = field_burn;
        out[2] = clamp01(field_load / 2.5);
        out[3] = field_burn;
        out[4] = vividness;
        out[5] = surface_weather_gate;
        out[6] = 1.0 - surface_weather_gate;
        out[10] = -1.0;
        out[37] = 1.0;
        out[38] = 1.0;
        out[39] = incoming_block_risk;
        out[40] = ratio;
        out[41] = incoming_block_risk;
        out[42] = surface_weather_gate;
        out[VIEW_DIAGNOSTIC_OFFSET] = field_view;
        out[VIEW_DIAGNOSTIC_OFFSET + 1] = field_burn;
        out[VIEW_DIAGNOSTIC_OFFSET + 2] = field_load;
        out[VIEW_DIAGNOSTIC_OFFSET + 3] = field_material_load;
        out[VIEW_DIAGNOSTIC_OFFSET + 4] = dominant_family_code;
        out[VIEW_DIAGNOSTIC_OFFSET + 5] = high_view;
        out[VIEW_DIAGNOSTIC_OFFSET + 6] = mid_view;
        out[VIEW_DIAGNOSTIC_OFFSET + 7] = low_view;
        out[VIEW_DIAGNOSTIC_OFFSET + 8] = high_burn;
        out[VIEW_DIAGNOSTIC_OFFSET + 9] = mid_burn;
        out[VIEW_DIAGNOSTIC_OFFSET + 10] = low_burn;
        out[VIEW_DIAGNOSTIC_OFFSET + 11] = clamp01(local_view);
        out[VIEW_DIAGNOSTIC_OFFSET + 12] = clamp01(local_burn);
        out[VIEW_DIAGNOSTIC_OFFSET + 13] = (2.5 * local_load).min(2.5);
        out[VIEW_DIAGNOSTIC_OFFSET + 14] = ground_view;
        out[VIEW_DIAGNOSTIC_OFFSET + 15] = ground_burn;
        out[VIEW_DIAGNOSTIC_OFFSET + 16] = ground_load;
        out[VIEW_DIAGNOSTIC_OFFSET + 17] = incoming_block_risk;
        out[VIEW_DIAGNOSTIC_OFFSET + 18] = (2.5 * local_material_load).min(2.5);
        out[VIEW_DIAGNOSTIC_OFFSET + 19] = field_incoming_aerosol_load;
        out[VIEW_DIAGNOSTIC_OFFSET + 20] = (2.5 * local_incoming_aerosol_load).min(2.5);
        out[VIEW_DIAGNOSTIC_OFFSET + 21] = target_deck_connected;
        out[VIEW_DIAGNOSTIC_OFFSET + 22] = target_deck_attenuated;
        out[VIEW_DIAGNOSTIC_OFFSET + 23] = target_deep_risk;
        out[VIEW_DIAGNOSTIC_OFFSET + 24] = cloud_optical_depth;
        out[VIEW_DIAGNOSTIC_OFFSET + 25] = peak_blocking;
        out[VIEW_DIAGNOSTIC_OFFSET + 26] = clear_gap_count;
        out[VIEW_DIAGNOSTIC_OFFSET + 27] = dominant_base_asl_mean_m;
        out[VIEW_DIAGNOSTIC_OFFSET + 28] = dominant_top_asl_mean_m;
        out[VIEW_DIAGNOSTIC_OFFSET + 29] = dominant_target_terrain_asl_mean_m;
        out[VIEW_DIAGNOSTIC_OFFSET + 30] = field_observer_aerosol_optical_depth;
        out[VIEW_DIAGNOSTIC_OFFSET + 31] = field_observer_aerosol_transmission;
        out[VIEW_DIAGNOSTIC_OFFSET + 32] = local_return_aerosol_optical_depth;
        out[VIEW_DIAGNOSTIC_OFFSET + 33] = local_return_aerosol_transmission;
        out[VETO_CANDIDATE_OFFSET] = candidate_field;
        out[VETO_TARGET_ELIGIBLE_OFFSET] = target_eligible_field;
        out[VETO_GEOMETRY_REACHABLE_OFFSET] = geometry_reachable_field;
        out[VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET] = external_path_transmission_field;
        out[VETO_UNOCCLUDED_BURN_UPPER_OFFSET] = unoccluded_burn_upper;
        if let Some(output) = target_output.take() {
            write_target_audit_records(target_accumulators, output, obs_elevation_m);
        }
    }
}

struct AerosolInputValidation {
    interpolation_fast_path_safe: bool,
    all_zero: bool,
}

fn strict_aerosol_inputs_are_valid(
    grid: &[f32],
    aerosol_profiles: &[f32],
    time_count: usize,
    cell_count: usize,
) -> Option<AerosolInputValidation> {
    let mut interpolation_fast_path_safe = true;
    let mut all_zero = true;
    for index in 0..time_count.saturating_mul(cell_count) {
        let aod550 = grid[index * GRID_FIELDS + AEROSOL_AOD550];
        if !aod550.is_finite() || !(0.0..=10.0).contains(&aod550) {
            return None;
        }
        let profile_start = index * AEROSOL_PROFILE_LEVELS;
        let profile_end = profile_start + AEROSOL_PROFILE_LEVELS;
        let mut has_nonzero_profile = false;
        for value in &aerosol_profiles[profile_start..profile_end] {
            if *value > f32::MAX / 8.0 {
                interpolation_fast_path_safe = false;
            }
            if !value.is_finite() || *value < 0.0 {
                return None;
            }
            has_nonzero_profile |= *value > 0.0;
            all_zero &= *value == 0.0;
        }
        if aod550 > 0.0 && !has_nonzero_profile {
            return None;
        }
    }
    Some(AerosolInputValidation {
        interpolation_fast_path_safe,
        all_zero,
    })
}

#[inline]
fn observer_work_signature(
    observers: &[f32],
    samples: &[f32],
    observer_index: usize,
    step_count: usize,
) -> u64 {
    let observer_start = observer_index * OBSERVER_FIELDS;
    let sample_start = observer_index * step_count * SAMPLE_FIELDS;
    let sample_end = sample_start + step_count * SAMPLE_FIELDS;
    let mut hash = 0xcbf29ce484222325u64;
    for value in observers[observer_start..observer_start + OBSERVER_FIELDS]
        .iter()
        .chain(samples[sample_start..sample_end].iter())
    {
        hash ^= u64::from(value.to_bits());
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

#[inline]
fn observer_work_matches(
    observers: &[f32],
    samples: &[f32],
    left_observer: usize,
    right_observer: usize,
    step_count: usize,
) -> bool {
    let left_observer_start = left_observer * OBSERVER_FIELDS;
    let right_observer_start = right_observer * OBSERVER_FIELDS;
    let observers_match = observers[left_observer_start..left_observer_start + OBSERVER_FIELDS]
        .iter()
        .zip(observers[right_observer_start..right_observer_start + OBSERVER_FIELDS].iter())
        .all(|(left, right)| left.to_bits() == right.to_bits());
    if !observers_match {
        return false;
    }
    let left_sample_start = left_observer * step_count * SAMPLE_FIELDS;
    let right_sample_start = right_observer * step_count * SAMPLE_FIELDS;
    samples[left_sample_start..left_sample_start + step_count * SAMPLE_FIELDS]
        .iter()
        .zip(samples[right_sample_start..right_sample_start + step_count * SAMPLE_FIELDS].iter())
        .all(|(left, right)| left.to_bits() == right.to_bits())
}

fn build_observer_work_canonical(
    observers: &[f32],
    samples: &[f32],
    observer_count: usize,
    step_count: usize,
) -> Vec<usize> {
    if observer_count < 2
        || std::env::var_os("FIRESKY_NATIVE_DISABLE_OBSERVER_WORK_REUSE").is_some()
    {
        return (0..observer_count).collect();
    }
    let mut canonical = Vec::with_capacity(observer_count);
    let mut buckets: HashMap<u64, usize> = HashMap::with_capacity(observer_count);
    for observer_index in 0..observer_count {
        let signature = observer_work_signature(observers, samples, observer_index, step_count);
        let canonical_index = match buckets.get(&signature).copied() {
            Some(candidate)
                if observer_work_matches(
                    observers,
                    samples,
                    candidate,
                    observer_index,
                    step_count,
                ) =>
            {
                candidate
            }
            _ => observer_index,
        };
        canonical.push(canonical_index);
        buckets.entry(signature).or_insert(observer_index);
    }
    canonical
}

#[allow(clippy::too_many_arguments)]
#[cfg(test)]
pub fn assess_batch(
    grid: &[f32],
    aerosol_profiles: &[f32],
    aerosol_level_count: usize,
    time_count: usize,
    cell_count: usize,
    observers: &[f32],
    observer_count: usize,
    samples: &[f32],
    sample_count: usize,
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    output: &mut [f32],
) -> bool {
    assess_batch_with_status(
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
    )
    .is_ok()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NationalAssessmentError {
    InvalidDimensions,
    InvalidAerosolInputs,
    Cancelled,
}

#[allow(clippy::too_many_arguments)]
#[allow(dead_code)]
pub fn assess_batch_with_status(
    grid: &[f32],
    aerosol_profiles: &[f32],
    aerosol_level_count: usize,
    time_count: usize,
    cell_count: usize,
    observers: &[f32],
    observer_count: usize,
    samples: &[f32],
    sample_count: usize,
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    output: &mut [f32],
) -> Result<(), NationalAssessmentError> {
    assess_batch_with_cancel(
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
        &BatchCancel::default(),
    )
}

#[allow(clippy::too_many_arguments)]
pub fn assess_batch_with_cancel(
    grid: &[f32],
    aerosol_profiles: &[f32],
    aerosol_level_count: usize,
    time_count: usize,
    cell_count: usize,
    observers: &[f32],
    observer_count: usize,
    samples: &[f32],
    sample_count: usize,
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    output: &mut [f32],
    cancel: &BatchCancel,
) -> Result<(), NationalAssessmentError> {
    assess_batch_internal(
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
        cancel,
    )
}

#[allow(clippy::too_many_arguments)]
fn assess_batch_internal(
    grid: &[f32],
    aerosol_profiles: &[f32],
    aerosol_level_count: usize,
    time_count: usize,
    cell_count: usize,
    observers: &[f32],
    observer_count: usize,
    samples: &[f32],
    sample_count: usize,
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    output: &mut [f32],
    cancel: &BatchCancel,
) -> Result<(), NationalAssessmentError> {
    if cancel.is_cancelled() {
        return Err(NationalAssessmentError::Cancelled);
    }
    if time_count == 0
        || rows.saturating_mul(cols) != cell_count
        || step_count == 0
        || observer_count.saturating_mul(step_count) != sample_count
        || grid.len()
            != time_count
                .saturating_mul(cell_count)
                .saturating_mul(GRID_FIELDS)
        || aerosol_level_count != AEROSOL_PROFILE_LEVELS
        || aerosol_profiles.len()
            != time_count
                .saturating_mul(cell_count)
                .saturating_mul(AEROSOL_PROFILE_LEVELS)
        || observers.len() != observer_count.saturating_mul(OBSERVER_FIELDS)
        || samples.len() != sample_count.saturating_mul(SAMPLE_FIELDS)
        || output.len() != sample_count.saturating_mul(OUTPUT_FIELDS)
    {
        return Err(NationalAssessmentError::InvalidDimensions);
    }
    let Some(aerosol_validation) =
        strict_aerosol_inputs_are_valid(grid, aerosol_profiles, time_count, cell_count)
    else {
        return Err(NationalAssessmentError::InvalidAerosolInputs);
    };
    let aerosol_interpolation_fast_path_safe = aerosol_validation.interpolation_fast_path_safe;
    let aerosol_empty = aerosol_validation.all_zero;
    // The template path trades repeated geometry work for a large shared
    // read-only structure. On the production national workload it increases
    // cache and memory pressure enough to make the hot loop slower. Keep it
    // available for targeted comparison, but require an explicit opt-in so
    // the measured faster path is the default.
    let observer_geometry_templates =
        if std::env::var_os("FIRESKY_NATIVE_ENABLE_GEOMETRY_TEMPLATES").is_some()
            && std::env::var_os("FIRESKY_NATIVE_DISABLE_GEOMETRY_TEMPLATES").is_none()
        {
            ObserverGeometryTemplates::build(
                observers,
                observer_count,
                rows,
                cols,
                lat0,
                lon0,
                spacing,
                radius_km,
            )
        } else {
            None
        };
    #[allow(unused_mut)]
    let mut batch = Batch {
        grid,
        aerosol_profiles,
        time_count,
        cell_count,
        observers,
        samples,
        step_count,
        rows,
        cols,
        lat0,
        lon0,
        spacing,
        radius_km,
        observer_geometry_templates,
    };
    let observer_parallel_min_len = std::env::var("FIRESKY_NATIVE_PARALLEL_MIN_LEN")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(OBSERVER_PARALLEL_MIN_LEN);
    let phase_timer = std::time::Instant::now();
    let phase_log = |label: &str, started: &mut std::time::Instant| {
        eprintln!(
            "firesky_batch_phase {label} {:.1}ms observers={observer_count} cells={cell_count}",
            started.elapsed().as_secs_f64() * 1000.0
        );
        *started = std::time::Instant::now();
    };
    let mut phase_start = phase_timer;
    let shared_solar_path_nodes = batch.build_shared_solar_path_nodes();
    phase_log("shared_solar_path_nodes", &mut phase_start);
    if cancel.is_cancelled() {
        return Err(NationalAssessmentError::Cancelled);
    }

    let shared_solar_path_node_sets = batch.build_shared_solar_path_node_sets();
    phase_log("shared_solar_path_node_sets", &mut phase_start);
    if cancel.is_cancelled() {
        return Err(NationalAssessmentError::Cancelled);
    }
    let shared_winds = batch.build_shared_wind_cache();
    phase_log("shared_winds", &mut phase_start);
    if cancel.is_cancelled() {
        return Err(NationalAssessmentError::Cancelled);
    }
    let shared_motion = batch.build_shared_motion_cache(cancel);
    phase_log("shared_motion", &mut phase_start);
    if cancel.is_cancelled() {
        return Err(NationalAssessmentError::Cancelled);
    }
    let shared_terrain = batch.build_shared_terrain_cache();
    phase_log("shared_terrain", &mut phase_start);
    if cancel.is_cancelled() {
        return Err(NationalAssessmentError::Cancelled);
    }
    let shared_solar_path_samples = batch.build_shared_solar_path_samples(
        shared_solar_path_nodes.as_ref(),
        shared_motion.as_ref(),
        shared_terrain.as_ref(),
        aerosol_empty,
        cancel,
    );
    phase_log("shared_solar_path_samples", &mut phase_start);
    if cancel.is_cancelled() {
        return Err(NationalAssessmentError::Cancelled);
    }
    let shared_solar_path_sample_sets = batch.build_shared_solar_path_sample_sets(
        shared_solar_path_node_sets.as_ref(),
        shared_motion.as_ref(),
        shared_terrain.as_ref(),
        aerosol_empty,
        cancel,
    );
    phase_log("shared_solar_path_sample_sets", &mut phase_start);
    if cancel.is_cancelled() {
        return Err(NationalAssessmentError::Cancelled);
    }
    let solar_ray_diagnostics = std::env::var_os("FIRESKY_NATIVE_DIAG_SOLAR_RAY_BUNDLES")
        .map(|_| Arc::new(SolarRayDiagnostics::new()));
    let duplicate_sample_cache_enabled =
        std::env::var_os("FIRESKY_NATIVE_DISABLE_DUPLICATE_SAMPLE_CACHE").is_none();
    let observer_work_canonical =
        build_observer_work_canonical(observers, samples, observer_count, step_count);
    let observer_valid_ranges: Vec<(usize, usize)> = (0..observer_count)
        .map(|observer_index| {
            let start = observer_index * step_count;
            let end = start + step_count;
            let first = (start..end)
                .find(|sample_index| samples[*sample_index * SAMPLE_FIELDS + SAMPLE_VALID] >= 0.5);
            let last = (start..end)
                .rfind(|sample_index| samples[*sample_index * SAMPLE_FIELDS + SAMPLE_VALID] >= 0.5);
            match (first, last) {
                (Some(first), Some(last)) => (first - start, last - start + 1),
                _ => (0, 0),
            }
        })
        .collect();
    let observer_output_stride = step_count * OUTPUT_FIELDS;
    output.fill(0.0);
    let cancelled_during_work = AtomicBool::new(false);
    output
        .par_chunks_mut(step_count * OUTPUT_FIELDS)
        .with_min_len(observer_parallel_min_len)
        .enumerate()
        .for_each_init(
            || {
                let mut buffers = SkyBuffers::with_shared_caches(
                    shared_winds.clone(),
                    shared_terrain.clone(),
                    shared_motion.clone(),
                    shared_solar_path_nodes.clone(),
                    shared_solar_path_node_sets.clone(),
                    shared_solar_path_samples.clone(),
                    shared_solar_path_sample_sets.clone(),
                    solar_ray_diagnostics.clone(),
                );
                buffers.aerosol_interpolation_fast_path_safe = aerosol_interpolation_fast_path_safe;
                buffers
            },
            |buffers, (observer_index, rows)| {
                if cancel.is_cancelled() {
                    cancelled_during_work.store(true, AtomicOrdering::Relaxed);
                    return;
                }
                if observer_work_canonical[observer_index] != observer_index {
                    return;
                }
                buffers.aerosol_empty = aerosol_empty;
                buffers.begin_observer_cache();
                let mut previous_sample_bits = [0u32; SAMPLE_FIELDS];
                let mut previous_sample_valid = false;
                let mut previous_output = [0.0f32; OUTPUT_FIELDS];
                let (valid_start, valid_stop) = observer_valid_ranges[observer_index];
                let mut step_index = valid_start;
                while step_index < valid_stop {
                    if step_index % 4 == 0 && cancel.is_cancelled() {
                        cancelled_during_work.store(true, AtomicOrdering::Relaxed);
                        return;
                    }
                    let offset = step_index * OUTPUT_FIELDS;
                    let sample_index = observer_index * step_count + step_index;
                    let sample_start = sample_index * SAMPLE_FIELDS;
                    let sample = &batch.samples[sample_start..sample_start + SAMPLE_FIELDS];
                    let duplicate_sample = duplicate_sample_cache_enabled
                        && previous_sample_valid
                        && sample
                            .iter()
                            .enumerate()
                            .all(|(index, value)| value.to_bits() == previous_sample_bits[index]);
                    if duplicate_sample {
                        rows[offset..offset + OUTPUT_FIELDS].copy_from_slice(&previous_output);
                        step_index += 1;
                        continue;
                    }
                    batch.assess_observer_sky_v2(
                        sample_index,
                        &mut rows[offset..offset + OUTPUT_FIELDS],
                        buffers,
                    );
                    for (index, value) in sample.iter().enumerate() {
                        previous_sample_bits[index] = value.to_bits();
                    }
                    previous_output.copy_from_slice(&rows[offset..offset + OUTPUT_FIELDS]);
                    previous_sample_valid = true;
                    step_index += 1;
                }
            },
        );
    if cancelled_during_work.load(AtomicOrdering::Relaxed) || cancel.is_cancelled() {
        return Err(NationalAssessmentError::Cancelled);
    }
    phase_log("observer_work", &mut phase_start);
    for (observer_index, &canonical_index) in observer_work_canonical.iter().enumerate() {
        if canonical_index == observer_index {
            continue;
        }
        let source_start = canonical_index * observer_output_stride;
        let target_start = observer_index * observer_output_stride;
        output.copy_within(
            source_start..source_start + observer_output_stride,
            target_start,
        );
    }
    if let Some(diagnostics) = solar_ray_diagnostics {
        eprintln!("solar_ray_bundle_diag {}", diagnostics.report());
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
pub fn audit_selected_batch(
    grid: &[f32],
    aerosol_profiles: &[f32],
    aerosol_level_count: usize,
    time_count: usize,
    cell_count: usize,
    observers: &[f32],
    observer_count: usize,
    samples: &[f32],
    sample_count: usize,
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    audit_indices: &[usize],
    output: &mut [f32],
) -> bool {
    if time_count == 0
        || rows.saturating_mul(cols) != cell_count
        || step_count == 0
        || observer_count.saturating_mul(step_count) != sample_count
        || grid.len()
            != time_count
                .saturating_mul(cell_count)
                .saturating_mul(GRID_FIELDS)
        || aerosol_level_count != AEROSOL_PROFILE_LEVELS
        || aerosol_profiles.len()
            != time_count
                .saturating_mul(cell_count)
                .saturating_mul(AEROSOL_PROFILE_LEVELS)
        || observers.len() != observer_count.saturating_mul(OBSERVER_FIELDS)
        || samples.len() != sample_count.saturating_mul(SAMPLE_FIELDS)
        || output.len() != audit_indices.len().saturating_mul(PATH_AUDIT_FIELDS)
        || audit_indices.iter().any(|index| *index >= sample_count)
    {
        return false;
    }
    let Some(aerosol_validation) =
        strict_aerosol_inputs_are_valid(grid, aerosol_profiles, time_count, cell_count)
    else {
        return false;
    };
    let batch = Batch {
        grid,
        aerosol_profiles,
        time_count,
        cell_count,
        observers,
        samples,
        step_count,
        rows,
        cols,
        lat0,
        lon0,
        spacing,
        radius_km,
        observer_geometry_templates: None,
    };
    output
        .par_chunks_mut(PATH_AUDIT_FIELDS)
        .enumerate()
        .for_each_init(SkyBuffers::new, |buffers, (output_index, row)| {
            buffers.aerosol_interpolation_fast_path_safe =
                aerosol_validation.interpolation_fast_path_safe;
            buffers.begin_observer_cache();
            let mut frame_row = [0.0f32; OUTPUT_FIELDS];
            let mut summary = PathAuditSummary::default();
            batch.assess_observer_sky_v2_with_audit(
                audit_indices[output_index],
                &mut frame_row,
                buffers,
                &mut summary,
            );
            summary.write_to(row);
        });
    true
}

#[allow(clippy::too_many_arguments)]
pub fn audit_selected_targets(
    grid: &[f32],
    aerosol_profiles: &[f32],
    aerosol_level_count: usize,
    time_count: usize,
    cell_count: usize,
    observers: &[f32],
    observer_count: usize,
    samples: &[f32],
    sample_count: usize,
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    audit_indices: &[usize],
    output: &mut [f32],
) -> bool {
    if time_count == 0
        || rows.saturating_mul(cols) != cell_count
        || step_count == 0
        || observer_count.saturating_mul(step_count) != sample_count
        || grid.len()
            != time_count
                .saturating_mul(cell_count)
                .saturating_mul(GRID_FIELDS)
        || aerosol_level_count != AEROSOL_PROFILE_LEVELS
        || aerosol_profiles.len()
            != time_count
                .saturating_mul(cell_count)
                .saturating_mul(AEROSOL_PROFILE_LEVELS)
        || observers.len() != observer_count.saturating_mul(OBSERVER_FIELDS)
        || samples.len() != sample_count.saturating_mul(SAMPLE_FIELDS)
        || output.len()
            != audit_indices
                .len()
                .saturating_mul(TARGET_AUDIT_MAX_RECORDS)
                .saturating_mul(TARGET_AUDIT_FIELDS)
        || audit_indices.iter().any(|index| *index >= sample_count)
    {
        return false;
    }
    let Some(aerosol_validation) =
        strict_aerosol_inputs_are_valid(grid, aerosol_profiles, time_count, cell_count)
    else {
        return false;
    };
    let batch = Batch {
        grid,
        aerosol_profiles,
        time_count,
        cell_count,
        observers,
        samples,
        step_count,
        rows,
        cols,
        lat0,
        lon0,
        spacing,
        radius_km,
        observer_geometry_templates: None,
    };
    let stride = TARGET_AUDIT_MAX_RECORDS * TARGET_AUDIT_FIELDS;
    output.par_chunks_mut(stride).enumerate().for_each_init(
        SkyBuffers::new,
        |buffers, (output_index, rows)| {
            buffers.aerosol_interpolation_fast_path_safe =
                aerosol_validation.interpolation_fast_path_safe;
            buffers.begin_observer_cache();
            let mut frame_row = [0.0f32; OUTPUT_FIELDS];
            batch.assess_observer_sky_v2_with_target_audit(
                audit_indices[output_index],
                &mut frame_row,
                buffers,
                rows,
            );
        },
    );
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn temporal_cache_weight_remains_dimensionless_after_quantization() {
        for (left, right, weight) in [(0, 1, 0.37), (0, 2, 0.37), (2, 5, 0.625)] {
            let key = temporal_cache_key(
                left,
                right,
                weight,
                600.0,
                900.0,
                TemporalCacheMode::Quantized120,
            );
            let reconstructed = f32::from_bits(key.weight_bits);
            let span = if right > left {
                ((right - left) * 3600) as f32
            } else {
                3600.0
            };
            assert!((0.0..=1.0).contains(&reconstructed));
            assert!((reconstructed - weight).abs() <= 60.0 / span);
            assert_eq!(f32::from_bits(key.left_seconds_bits), 600.0);
            assert_eq!(f32::from_bits(key.right_seconds_bits), 960.0);
        }
    }

    #[test]
    fn temporal_cache_quantization_has_expected_endpoints_and_ties() {
        let endpoints = [0.0, 1.0];
        for weight in endpoints {
            let key =
                temporal_cache_key(0, 1, weight, 0.0, 3600.0, TemporalCacheMode::Quantized120);
            assert_eq!(f32::from_bits(key.weight_bits), weight);
            assert_eq!(f32::from_bits(key.left_seconds_bits), 0.0);
            assert_eq!(f32::from_bits(key.right_seconds_bits), 3600.0);
        }
        // Test half-bucket ties on the seconds inputs, which are exactly
        // representable. A 60 s weight is 1/60 and cannot be an exact f32
        // tie; 0.02 (72 s) unambiguously selects the 120 s weight bucket.
        let key = temporal_cache_key(0, 1, 0.02, 60.0, 180.0, TemporalCacheMode::Quantized120);
        assert_eq!(f32::from_bits(key.weight_bits), 1.0 / 30.0);
        assert_eq!(f32::from_bits(key.left_seconds_bits), 0.0);
        assert_eq!(f32::from_bits(key.right_seconds_bits), 240.0);
    }

    #[test]
    fn exact_temporal_cache_keys_preserve_raw_input_bits() {
        let weight = f32::from_bits(0x3eaaaaab);
        let left_seconds = f32::from_bits(0x447a1234);
        let right_seconds = f32::from_bits(0x449a5678);
        let key = temporal_cache_key(
            2,
            5,
            weight,
            left_seconds,
            right_seconds,
            TemporalCacheMode::Exact,
        );
        assert_eq!(key.weight_bits, weight.to_bits());
        assert_eq!(key.left_seconds_bits, left_seconds.to_bits());
        assert_eq!(key.right_seconds_bits, right_seconds.to_bits());
        assert_ne!(
            key,
            temporal_cache_key(
                2,
                5,
                weight,
                left_seconds,
                right_seconds,
                TemporalCacheMode::Quantized120,
            )
        );
    }

    #[test]
    fn consistent_temporal_cache_conserves_bracket_span() {
        // Sweep weights across several bracket widths, including the wide
        // f120-style brackets. The reconstructed seconds must always sum to
        // the span and the weight must stay dimensionless.
        for (left, right) in [(0, 1), (0, 2), (2, 5), (0, 6)] {
            let span = if right > left {
                ((right - left) * 3600) as f32
            } else {
                3600.0
            };
            for step in 0..=48 {
                let weight = step as f32 / 48.0;
                let key = temporal_cache_key(
                    left,
                    right,
                    weight,
                    weight * span,
                    (1.0 - weight) * span,
                    TemporalCacheMode::Consistent120,
                );
                let (w, l, r) = temporal_cache_inputs(key);
                assert_eq!(l + r, span, "span not conserved at weight {weight}");
                assert_eq!(w, l / span);
                assert!((0.0..=1.0).contains(&w));
                assert!((l - weight * span).abs() <= 60.0);
                // right = span - bucketed(elapsed); its error is the bucket
                // error (<=60 s) plus the f32 rounding of (1-weight)*span,
                // which reaches ~0.001 s on the widest 6 h bracket.
                assert!((r - (1.0 - weight) * span).abs() <= 60.01);
            }
        }
    }

    #[test]
    fn consistent_temporal_cache_preserves_endpoints_and_ties() {
        for weight in [0.0, 1.0] {
            let key =
                temporal_cache_key(0, 1, weight, 0.0, 3600.0, TemporalCacheMode::Consistent120);
            let (w, l, r) = temporal_cache_inputs(key);
            assert_eq!(w, weight);
            assert_eq!(l, weight * 3600.0);
            assert_eq!(r, (1.0 - weight) * 3600.0);
        }
        // Half-bucket ties use round-ties-even on the elapsed seconds. Use
        // exactly representable weights so weight*3600 lands precisely on the
        // tie: 1/24 -> 150 s -> 120, 1/12 -> 300 s -> 240, 1/6 -> 600 s -> 600.
        // (1/60 is not exact in f32; 60.000002 s rounds up to 120, which is the
        // expected floating-point boundary, not a conservation failure.)
        for (weight, expected) in [(1.0 / 24.0, 120.0), (1.0 / 12.0, 240.0), (1.0 / 6.0, 600.0)] {
            let elapsed = weight * 3600.0;
            let key = temporal_cache_key(
                0,
                1,
                weight,
                elapsed,
                3600.0 - elapsed,
                TemporalCacheMode::Consistent120,
            );
            let (_, l, r) = temporal_cache_inputs(key);
            assert_eq!(l, expected);
            assert_eq!(r, 3600.0 - expected);
        }
    }

    #[test]
    fn consistent_temporal_cache_merges_nearby_samples_into_one_bucket() {
        // Samples a few seconds apart inside one 120 s bucket must collapse to
        // a single key so the shared motion cache can reuse one dense table.
        let mut keys = Vec::new();
        for step in 0..8 {
            let weight = 0.36 + step as f32 * 0.001;
            keys.push(temporal_cache_key(
                0,
                1,
                weight,
                weight * 3600.0,
                (1.0 - weight) * 3600.0,
                TemporalCacheMode::Consistent120,
            ));
        }
        assert!(keys.windows(2).all(|pair| pair[0] == pair[1]));
        // A sample past the bucket boundary must produce a different key.
        let far = temporal_cache_key(
            0,
            1,
            0.5,
            1800.0,
            1800.0,
            TemporalCacheMode::Consistent120,
        );
        assert_ne!(keys[0], far);
    }

    #[test]
    fn consistent_and_quantized_modes_differ_on_inconsistent_inputs() {
        // Inconsistent raw inputs: weight 0.5 implies elapsed 1800 s, but the
        // supplied sides are 60/180 s (sum 240 != span 3600). The legacy mode
        // rounds each side independently (0/240 s) while keeping weight 0.5;
        // the consistent mode derives everything from the bucketed elapsed
        // time, so left + right == span and the two keys must differ.
        let consistent =
            temporal_cache_key(0, 1, 0.5, 60.0, 180.0, TemporalCacheMode::Consistent120);
        let quantized =
            temporal_cache_key(0, 1, 0.5, 60.0, 180.0, TemporalCacheMode::Quantized120);
        let (cw, cl, cr) = temporal_cache_inputs(consistent);
        assert_eq!(cl, 1800.0);
        assert_eq!(cr, 1800.0);
        assert_eq!(cl + cr, 3600.0);
        assert_eq!(cw, 0.5);
        let (qw, ql, qr) = temporal_cache_inputs(quantized);
        assert_eq!(ql, 0.0);
        assert_eq!(qr, 240.0);
        assert_ne!(ql + qr, 3600.0);
        assert_eq!(qw, 0.5);
        assert_ne!(consistent, quantized);
    }

    #[test]
    fn quantized_observer_outputs_are_independent_of_bucket_population_order() {
        if *TEMPORAL_CACHE_MODE != TemporalCacheMode::Quantized120 {
            return;
        }
        let (mut grid, observers, mut samples) = path_audit_fixture();
        grid.resize(2 * GRID_FIELDS, 0.0);
        grid.copy_within(0..GRID_FIELDS, GRID_FIELDS);
        for time in 0..2 {
            let offset = time * GRID_FIELDS;
            grid[offset + HIGH] = 0.45 + time as f32 * 0.35;
            grid[offset + AEROSOL_AOD550] = 0.1 + time as f32 * 0.8;
            grid[offset + TERRAIN_ELEVATION] = 100.0 + time as f32 * 50.0;
            grid[offset + HIGH_U] = 8.0 + time as f32 * 4.0;
            grid[offset + HIGH_V] = -3.0;
        }
        samples.resize(4 * SAMPLE_FIELDS, 0.0);
        let weights = [0.36, 0.365, 0.37, 0.375];
        let left_seconds = [430.0, 440.0, 450.0, 460.0];
        let right_seconds = [790.0, 800.0, 810.0, 820.0];
        let shared_key = temporal_cache_key(
            0,
            1,
            weights[0],
            left_seconds[0],
            right_seconds[0],
            TemporalCacheMode::Quantized120,
        );
        for step in 0..4 {
            let offset = step * SAMPLE_FIELDS;
            samples[offset] = -0.1;
            samples[offset + 1] = 90.0;
            samples[offset + 2] = 0.0;
            samples[offset + 3] = 1.0;
            samples[offset + 4] = weights[step];
            samples[offset + SAMPLE_LEFT_SECONDS] = left_seconds[step];
            samples[offset + SAMPLE_RIGHT_SECONDS] = right_seconds[step];
            samples[offset + SAMPLE_VALID] = 1.0;
            assert_eq!(
                temporal_cache_key(
                    0,
                    1,
                    weights[step],
                    left_seconds[step],
                    right_seconds[step],
                    TemporalCacheMode::Quantized120,
                ),
                shared_key,
                "fixture step {step} must stay inside one temporal bucket"
            );
        }
        let profile = test_aerosol_profile();
        let aerosol_profiles = profile
            .into_iter()
            .chain(profile.map(|value| value * 2.0))
            .collect::<Vec<_>>();
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &aerosol_profiles,
            time_count: 2,
            cell_count: 1,
            observers: &observers,
            samples: &samples,
            step_count: 4,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut forward = SkyBuffers::new();
        let mut reverse = SkyBuffers::new();
        forward.begin_observer_cache();
        reverse.begin_observer_cache();
        let mut expected = vec![0.0f32; OUTPUT_FIELDS];
        batch.assess_observer_sky_v2(0, &mut expected, &mut forward);
        let mut actual = vec![0.0f32; OUTPUT_FIELDS];
        for step in [3, 2, 1, 0] {
            batch.assess_observer_sky_v2(step, &mut actual, &mut reverse);
            assert_eq!(
                actual.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
                expected.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
                "reverse step {step}"
            );
        }
        for step in 0..4 {
            batch.assess_observer_sky_v2(step, &mut actual, &mut forward);
            assert_eq!(
                actual.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
                expected.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
                "forward step {step}"
            );
        }
    }

    #[test]
    fn wrapped_grid_sampling_keeps_wind_and_rays_on_the_contiguous_axis() {
        let batch = Batch {
            grid: &[],
            aerosol_profiles: &[],
            time_count: 0,
            cell_count: 6,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 2,
            cols: 3,
            lat0: 0.,
            lon0: 179.,
            spacing: 1.,
            radius_km: 200.,
            observer_geometry_templates: None,
        };
        assert_eq!(
            batch.horizontal_weights(0.5, -179.5),
            batch.horizontal_weights(0.5, 180.5)
        );
        assert_eq!(
            batch.cloud_horizontal_weights(0.5, -179.5),
            Some([(1, 0.25), (2, 0.25), (4, 0.25), (5, 0.25)])
        );
        assert_eq!(cell_for_point(0., -179., 0., 179., 1., 2, 3), Some(2));
        assert_eq!(batch.clamp_to_grid_lon(-179.5), 180.5);
        assert_eq!(batch.clamp_to_grid_lon(-178.), 181.);
        assert_eq!(batch.clamp_to_grid_lon(178.), 179.);
        let (_, eastward) = batch.wind_displaced_position_with_wind(0.5, 179.99, 600., 1., 10., 0.);
        assert!(eastward > 180. && eastward < 181.);
        assert!(batch.cloud_horizontal_weights(0.5, eastward).is_some());
        let (lat, lon) = target_point(0.5, 179.5, 90., 100.);
        assert!(lon > 180. && lon < 181.);
        assert!(batch.cloud_horizontal_weights(lat, lon).is_some());
        assert_eq!(
            batch.horizontal_weights(lat, lon),
            batch.horizontal_weights(lat, lon - 360.)
        );
        assert_eq!(longitude_on_grid(179., -179., 1., 359), 179.);
        assert_eq!(longitude_on_grid(-180., -179., 1., 359), -180.);
        assert_eq!(longitude_on_grid(-180., 179., 1., 2), 180.);
        assert_eq!(longitude_on_grid(180., -180., 1., 2), -180.);
        for (lat, lon) in [
            (0.5, -179.5),
            (0.5, 180.5),
            (0.25, 179.0),
            (0.75, 181.0),
            (-0.1, 180.0),
            (1.1, 180.0),
        ] {
            let normalized = longitude_on_grid(lon, batch.lon0, batch.spacing, batch.cols);
            assert_eq!(
                batch.horizontal_weights(lat, lon).map(|(cell, weight)| (cell, weight.to_bits())),
                batch
                    .horizontal_weights_on_grid(lat, normalized)
                    .map(|(cell, weight)| (cell, weight.to_bits())),
                "normalized helper differs at {lat},{lon}"
            );
        }
    }

    #[test]
    fn material_lane_motion_blend_matches_original_scalar_bits() {
        let choices = [
            -0.0,
            0.0,
            -0.2,
            0.05,
            0.3,
            0.75,
            1.0,
            1.2,
            f32::from_bits(0x7fc00123),
            f32::from_bits(0xffc00456),
            f32::INFINITY,
            f32::NEG_INFINITY,
            f32::MAX,
            -f32::MAX,
            f32::from_bits(1),
            f32::MIN_POSITIVE,
            1.25,
            4_000.0,
            10_000.0,
            -100.0,
            -800.0,
        ];
        let horizontals = [
            [(0, 0.125), (1, 0.25), (2, 0.5), (3, 0.125)],
            [(0, 1.0), (1, 0.0), (2, -0.0), (3, -1.0)],
            [(3, 0.25), (2, 0.25), (1, 0.25), (0, 0.25)],
            [(0, 0.125), (0, 0.25), (0, 0.5), (0, 0.125)],
            [(4, 0.25), (0, 0.25), (1, 0.25), (2, 0.25)],
            [(0, f32::from_bits(1)), (1, 0.0), (2, 0.0), (3, 0.0)],
            [(0, f32::MAX), (1, f32::MAX), (2, f32::MAX), (3, f32::MAX)],
            [(0, f32::NAN), (1, 0.2), (2, 0.3), (3, 0.5)],
            [(0, f32::INFINITY), (1, 0.0), (2, 0.0), (3, 0.0)],
        ];
        let grid = vec![0.0; 2 * 4 * GRID_FIELDS];
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 2,
            cell_count: 4,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 2,
            cols: 2,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let bits = |clouds: [CloudLayerSample; 3]| {
            clouds.map(|cloud| {
                [
                    cloud.amount,
                    cloud.opacity,
                    cloud.material_quality,
                    cloud.base_m,
                    cloud.top_m,
                ]
                .map(f32::to_bits)
            })
        };
        let mut state = 0x64ba739du32;
        let mut buffers = SkyBuffers::new();
        let mut original_buffers = SkyBuffers::new();
        let mut cases = 0;
        for scenario in 0..128 {
            let mut value = || {
                state = state.wrapping_mul(1664525).wrapping_add(1013904223);
                choices[(state >> 16) as usize % choices.len()]
            };
            let clouds: [[CloudLayerSample; 3]; 4] = std::array::from_fn(|_| {
                std::array::from_fn(|_| CloudLayerSample {
                    amount: value(),
                    opacity: value(),
                    material_quality: value(),
                    base_m: value(),
                    top_m: value(),
                })
            });
            let weight = (scenario % 4) as f32 * 0.25;
            for current in [&mut buffers, &mut original_buffers] {
                current.begin_layer_cache_for_sample(4, 0, 1, weight, 600.0, 900.0);
                let generation = current.layer_cache_current_generation;
                current.motion_wind_all_generation.fill(generation);
                current.motion_state_cache_generation.fill(generation);
                current.motion_state_cache.fill(1);
                current.motion_layer_cache_generation.fill(generation);
                for cell in 0..4 {
                    for layer in 0..3 {
                        current.motion_layer_cache[cell * 3 + layer] = clouds[cell][layer];
                    }
                }
            }
            for horizontal in horizontals {
                let common_amount = horizontal.iter().all(|(_, weight)| weight.is_finite());
                for _ in 0..2 {
                    let original = if common_amount {
                        batch.cached_motion_cloud_layers_original_scalar_impl::<true>(
                            &mut original_buffers,
                            0,
                            1,
                            weight,
                            600.0,
                            900.0,
                            &horizontal,
                        )
                    } else {
                        batch.cached_motion_cloud_layers_original_scalar_impl::<false>(
                            &mut original_buffers,
                            0,
                            1,
                            weight,
                            600.0,
                            900.0,
                            &horizontal,
                        )
                    };
                    let actual = batch.cached_motion_cloud_layers_with_weights(
                        &mut buffers,
                        0,
                        1,
                        weight,
                        600.0,
                        900.0,
                        &horizontal,
                    );
                    assert_eq!(
                        bits(actual),
                        bits(original),
                        "scenario={scenario} horizontal={horizontal:?}"
                    );
                    cases += 1;
                }
            }
        }
        assert_eq!(cases, 2304);
    }

    #[test]
    fn common_motion_amount_preserves_original_bits_and_cache_state() {
        let choices = [
            -0.0,
            0.0,
            -0.2,
            0.05,
            0.3,
            0.75,
            1.0,
            1.2,
            f32::NAN,
            f32::INFINITY,
            f32::NEG_INFINITY,
            f32::MAX,
            -f32::MAX,
            f32::from_bits(1),
        ];
        let weights = [
            -0.0,
            0.0,
            0.35,
            1.0,
            -0.2,
            1.2,
            f32::MAX,
            f32::NAN,
            f32::INFINITY,
            f32::NEG_INFINITY,
        ];
        let horizontals = [
            [(0, 0.1), (1, 0.2), (2, 0.3), (3, 0.4)],
            [(0, 1.0), (1, 0.0), (2, -0.0), (3, -1.0)],
            [(3, 0.25), (2, 0.25), (1, 0.25), (0, 0.25)],
            [(0, f32::MAX), (1, f32::MAX), (2, f32::MAX), (3, f32::MAX)],
            [(0, f32::from_bits(1)), (1, 0.0), (2, 0.0), (3, 0.0)],
            [(0, f32::NAN), (1, 0.2), (2, 0.3), (3, 0.5)],
            [(0, f32::INFINITY), (1, 0.0), (2, 0.0), (3, 0.0)],
            [(4, 0.25), (0, 0.25), (1, 0.25), (2, 0.25)],
        ];
        let bits = |layers: [CloudLayerSample; 3]| {
            layers.map(|cloud| {
                [
                    cloud.amount,
                    cloud.opacity,
                    cloud.material_quality,
                    cloud.base_m,
                    cloud.top_m,
                ]
                .map(f32::to_bits)
            })
        };
        let mut state = 0x467bf591u32;
        let mut fast_cases = 0;
        let mut fallback_cases = 0;
        for scenario in 0..96 {
            let mut grid = vec![0.0; 2 * 4 * GRID_FIELDS];
            for value in &mut grid {
                state = state.wrapping_mul(1664525).wrapping_add(1013904223);
                *value = choices[(state >> 16) as usize % choices.len()];
            }
            if scenario < 48 {
                for cell in 0..8 {
                    let offset = cell * GRID_FIELDS;
                    for (amount, opacity, material, base, top, u, v) in [
                        (
                            HIGH,
                            HIGH_BLOCKER_OPACITY,
                            HIGH_MATERIAL,
                            HIGH_BASE,
                            HIGH_TOP,
                            HIGH_U,
                            HIGH_V,
                        ),
                        (
                            MID,
                            MID_BLOCKER_OPACITY,
                            MID_MATERIAL,
                            MID_BASE,
                            MID_TOP,
                            MID_U,
                            MID_V,
                        ),
                        (
                            LOW,
                            LOW_BLOCKER_OPACITY,
                            LOW_MATERIAL,
                            LOW_BASE,
                            LOW_TOP,
                            LOW_U,
                            LOW_V,
                        ),
                    ] {
                        grid[offset + amount] = [0.0, 0.1, 0.4, 1.0][(cell + scenario) % 4];
                        grid[offset + opacity] = 0.2;
                        grid[offset + material] = 0.7;
                        grid[offset + base] = 900.0 + amount as f32 * 2_000.0;
                        grid[offset + top] = grid[offset + base] + 1_500.0;
                        grid[offset + u] = if scenario % 3 == 0 { 0.0 } else { 8.0 };
                        grid[offset + v] = if scenario % 3 == 0 { 0.0 } else { -4.0 };
                    }
                }
            }
            let batch = Batch {
                grid: &grid,
                aerosol_profiles: &[],
                time_count: 2,
                cell_count: 4,
                observers: &[],
                samples: &[],
                step_count: 0,
                rows: 2,
                cols: 2,
                lat0: 0.0,
                lon0: 0.0,
                spacing: 1.0,
                radius_km: 100.0,
                observer_geometry_templates: None,
            };
            let mut actual_buffers = SkyBuffers::new();
            let mut original_buffers = SkyBuffers::new();
            for weight in weights {
                for (left_seconds, right_seconds) in [(0.0, 0.0), (600.0, 900.0)] {
                    for buffers in [&mut actual_buffers, &mut original_buffers] {
                        buffers.begin_layer_cache_for_sample(
                            4,
                            0,
                            1,
                            weight,
                            left_seconds,
                            right_seconds,
                        );
                    }
                    for horizontal in &horizontals {
                        if weight.is_finite()
                            && horizontal.iter().all(|(_, value)| value.is_finite())
                        {
                            fast_cases += 1;
                        } else {
                            fallback_cases += 1;
                        }
                        for _ in 0..2 {
                            let actual = batch.cached_motion_cloud_layers_with_weights(
                                &mut actual_buffers,
                                0,
                                1,
                                weight,
                                left_seconds,
                                right_seconds,
                                horizontal,
                            );
                            let original = batch
                                .cached_motion_cloud_layers_with_weights_impl::<false>(
                                    &mut original_buffers,
                                    0,
                                    1,
                                    weight,
                                    left_seconds,
                                    right_seconds,
                                    horizontal,
                                );
                            assert_eq!(bits(actual), bits(original),
                                "scenario={scenario} weight={weight:?} seconds={left_seconds}/{right_seconds}");
                        }
                    }
                    assert_eq!(
                        actual_buffers.motion_scalar_cache_generation,
                        original_buffers.motion_scalar_cache_generation
                    );
                    assert_eq!(
                        actual_buffers
                            .motion_scalar_cache
                            .iter()
                            .map(|value| value.to_bits())
                            .collect::<Vec<_>>(),
                        original_buffers
                            .motion_scalar_cache
                            .iter()
                            .map(|value| value.to_bits())
                            .collect::<Vec<_>>(),
                    );
                    assert_eq!(
                        actual_buffers.motion_state_cache,
                        original_buffers.motion_state_cache
                    );
                    assert_eq!(
                        actual_buffers.motion_layer_cache_generation,
                        original_buffers.motion_layer_cache_generation
                    );
                }
            }
        }
        assert!(fast_cases > 0 && fallback_cases > 0);
    }

    #[test]
    fn specialized_horizontal_cloud_sampling_preserves_dynamic_bits() {
        let choices = [
            -0.0,
            0.0,
            -0.2,
            0.05,
            0.3,
            0.75,
            1.0,
            1.2,
            f32::NAN,
            f32::INFINITY,
            f32::NEG_INFINITY,
        ];
        let horizontals = [
            [(0, 0.1), (1, 0.2), (2, 0.3), (3, 0.4)],
            [(0, 1.0), (1, 0.0), (2, -0.0), (3, -1.0)],
            [(3, 0.25), (2, 0.25), (1, 0.25), (0, 0.25)],
            [(0, f32::NAN), (1, 0.2), (2, 0.3), (3, 0.5)],
            [(0, f32::INFINITY), (1, 0.0), (2, 0.0), (3, 0.0)],
        ];
        let bits = |(cloud, scalar): (CloudLayerSample, f32)| {
            [
                cloud.amount,
                cloud.opacity,
                cloud.material_quality,
                cloud.base_m,
                cloud.top_m,
                scalar,
            ]
            .map(f32::to_bits)
        };
        let mut state = 0x742a5b3du32;
        for scenario in 0..128 {
            let mut grid = vec![0.0; 2 * 4 * GRID_FIELDS];
            for value in &mut grid {
                state = state.wrapping_mul(1664525).wrapping_add(1013904223);
                *value = choices[(state >> 16) as usize % choices.len()];
            }
            for cell in 0..8 {
                let offset = cell * GRID_FIELDS;
                let base = 300.0 + cell as f32 * 80.0;
                let top = if scenario % 2 == 0 {
                    base - 10.0
                } else {
                    base + 1_500.0
                };
                for (base_field, top_field) in [
                    (HIGH_BASE, HIGH_TOP),
                    (MID_BASE, MID_TOP),
                    (LOW_BASE, LOW_TOP),
                    (BASE, TOP),
                ] {
                    grid[offset + base_field] = base;
                    grid[offset + top_field] = top;
                }
                grid[offset + PRESSURE] = [300.0, 550.0, 900.0, f32::NAN][cell % 4];
            }
            if scenario < 64 {
                for cell in 0..8 {
                    let offset = cell * GRID_FIELDS;
                    grid[offset + PRESSURE] = 550.0;
                    for (amount, opacity, material, base, top) in [
                        (
                            HIGH,
                            HIGH_BLOCKER_OPACITY,
                            HIGH_MATERIAL,
                            HIGH_BASE,
                            HIGH_TOP,
                        ),
                        (MID, MID_BLOCKER_OPACITY, MID_MATERIAL, MID_BASE, MID_TOP),
                        (LOW, LOW_BLOCKER_OPACITY, LOW_MATERIAL, LOW_BASE, LOW_TOP),
                    ] {
                        grid[offset + amount] = 0.4 + cell as f32 * 0.03;
                        grid[offset + opacity] = 0.2;
                        grid[offset + material] = 0.7;
                        if scenario < 60 && cell % 4 == (scenario / 5) % 4 {
                            let field = [base, top, PRESSURE, opacity, material][scenario % 5];
                            grid[offset + field] = if scenario < 40 {
                                f32::NAN
                            } else {
                                f32::INFINITY
                            };
                        }
                    }
                }
            }
            let batch = Batch {
                grid: &grid,
                aerosol_profiles: &[],
                time_count: 2,
                cell_count: 4,
                observers: &[],
                samples: &[],
                step_count: 0,
                rows: 2,
                cols: 2,
                lat0: 0.0,
                lon0: 0.0,
                spacing: 0.25,
                radius_km: 50.0,
                observer_geometry_templates: None,
            };
            for horizontal in &horizontals {
                for time in 0..2 {
                    for layer in 0..5 {
                        let actual = batch
                            .cloud_layer_sample_at_time_with_horizontal_and_scalar::<true>(
                                time,
                                horizontal,
                                layer,
                                HIGH_TARGET_BODY_RISK,
                            );
                        let reference = batch
                            .cloud_layer_sample_at_time_with_horizontal_and_scalar_impl::<true, 3>(
                                time,
                                horizontal,
                                layer,
                                HIGH_TARGET_BODY_RISK,
                            );
                        assert_eq!(
                            bits(actual),
                            bits(reference),
                            "scalar included, scenario={scenario} time={time} layer={layer}",
                        );
                        let actual = batch
                            .cloud_layer_sample_at_time_with_horizontal_and_scalar::<false>(
                                time,
                                horizontal,
                                layer,
                                HIGH_TARGET_BODY_RISK,
                            );
                        let reference = batch
                            .cloud_layer_sample_at_time_with_horizontal_and_scalar_impl::<false, 3>(
                                time,
                                horizontal,
                                layer,
                                HIGH_TARGET_BODY_RISK,
                            );
                        assert_eq!(
                            bits(actual),
                            bits(reference),
                            "scalar excluded, scenario={scenario} time={time} layer={layer}",
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn squared_radius_prefilter_keeps_hypot_boundary_band() {
        let radius = 600.0f32;
        let north = 247.33875f32;
        let east = 546.6476f32;
        let radius_sq = radius * radius;
        let distance_sq = north * north + east * east;
        let distance = north.hypot(east);

        assert!(distance_sq > radius_sq);
        assert!(radius_squared_prefilter_allows(distance_sq, radius_sq));
        assert!(distance <= radius);
        assert_eq!(
            radius_distance_km(north, east, radius, radius_sq),
            Some((distance_sq, distance))
        );
    }

    fn test_aerosol_profile() -> [f32; AEROSOL_PROFILE_LEVELS] {
        [
            2.0e-5, 1.9e-5, 1.8e-5, 1.7e-5, 1.5e-5, 1.3e-5, 1.0e-5, 7.5e-6, 5.0e-6, 3.0e-6, 1.0e-6,
        ]
    }

    #[test]
    fn spherical_observer_chord_accounts_for_elevated_station() {
        let middle = spherical_line_of_sight_height_m(4011.0, 7615.0, 225.0, 100.0);

        assert!(
            (spherical_line_of_sight_height_m(4011.0, 7615.0, 225.0, 0.0) - 4011.0).abs() < 1e-3
        );
        assert!(
            (spherical_line_of_sight_height_m(4011.0, 7615.0, 225.0, 225.0) - 7615.0).abs() < 1e-3
        );
        assert!(middle > 4500.0 && middle < 4800.0);
    }

    #[test]
    fn observer_cell_aerosol_starts_at_station_altitude() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[TERRAIN_ELEVATION] = 4224.0;
        grid[AEROSOL_AOD550] = 0.5;
        let aerosol_profiles = test_aerosol_profile();
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &aerosol_profiles,
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 32.5,
            lon0: 99.0,
            spacing: 0.25,
            radius_km: 600.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(1);

        let path = batch.observer_cloud_base_aerosol_optical_depth(
            0,
            0,
            0.0,
            0.0,
            0.0,
            32.5,
            99.0,
            4011.0,
            0.0,
            285.0,
            5180.0,
            HIGH,
            true,
            &mut buffers,
        );
        let expected = AerosolColumn {
            aod550: 0.5,
            extinction_m_inv: aerosol_profiles,
        }
        .vertical_optical_depth(4011.0, 5180.0, 4011.0);

        assert!((path.optical_depth - expected).abs() < 1e-6);
        assert!(path.optical_depth > 0.0);
    }

    #[test]
    fn observer_cell_aerosol_keeps_a_negative_asl_station_and_nonnegative_agl() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[TERRAIN_ELEVATION] = -120.0;
        grid[AEROSOL_AOD550] = 0.5;
        let aerosol_profiles = test_aerosol_profile();
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &aerosol_profiles,
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 42.75,
            lon0: 89.25,
            spacing: 0.25,
            radius_km: 600.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(1);

        let path = batch.observer_cloud_base_aerosol_optical_depth(
            0,
            0,
            0.0,
            0.0,
            0.0,
            42.75,
            89.25,
            -120.0,
            0.0,
            285.0,
            1_000.0,
            HIGH,
            true,
            &mut buffers,
        );
        let expected = AerosolColumn {
            aod550: 0.5,
            extinction_m_inv: aerosol_profiles,
        }
        .vertical_optical_depth(-120.0, 1_000.0, -120.0);

        assert!((path.optical_depth - expected).abs() < 1e-6);
        assert!(path.optical_depth > 0.0);
    }

    #[test]
    fn low_cloud_on_observer_return_path_reduces_mid_cloud_transmission() {
        let cell_count = 3;
        let mut cloudy_grid = vec![0.0f32; cell_count * GRID_FIELDS];
        for cell in 0..cell_count {
            let offset = cell * GRID_FIELDS;
            cloudy_grid[offset + LOW] = 0.8;
            cloudy_grid[offset + LOW_MATERIAL] = 1.0;
            cloudy_grid[offset + LOW_BLOCKER_OPACITY] = 0.9;
            cloudy_grid[offset + LOW_BASE] = 1_000.0;
            cloudy_grid[offset + LOW_TOP] = 3_000.0;
        }
        let clear_grid = vec![0.0f32; cell_count * GRID_FIELDS];
        let aerosol_profiles = vec![0.0f32; cell_count * AEROSOL_PROFILE_LEVELS];
        let make_path = |grid: &[f32]| {
            let batch = Batch {
                grid,
                aerosol_profiles: &aerosol_profiles,
                time_count: 1,
                cell_count,
                observers: &[],
                samples: &[],
                step_count: 0,
                rows: 1,
                cols: cell_count,
                lat0: 0.0,
                lon0: 0.0,
                spacing: 0.25,
                radius_km: 250.0,
                observer_geometry_templates: None,
            };
            let mut buffers = SkyBuffers::new();
            buffers.begin_layer_cache(cell_count);
            batch.observer_cloud_base_aerosol_optical_depth(
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                55.5,
                90.0,
                5_000.0,
                MID,
                false,
                &mut buffers,
            )
        };

        let clear_path = make_path(&clear_grid);
        let cloudy_path = make_path(&cloudy_grid);

        assert_eq!(clear_path.cloud_transmission, 1.0);
        assert!(cloudy_path.cloud_transmission < clear_path.cloud_transmission);
        assert!(cloudy_path.cloud_transmission > 0.0);
    }

    #[test]
    fn low_cloud_on_incoming_solar_path_reduces_mid_cloud_transmission() {
        let cell_count = 8;
        let mut cloudy_grid = vec![0.0f32; cell_count * GRID_FIELDS];
        for cell in 0..cell_count {
            let offset = cell * GRID_FIELDS;
            cloudy_grid[offset + TERRAIN_ELEVATION] = -30_000.0;
        }
        let blocker = 4 * GRID_FIELDS;
        cloudy_grid[blocker + LOW] = 1.0;
        cloudy_grid[blocker + LOW_MATERIAL] = 1.0;
        cloudy_grid[blocker + LOW_BLOCKER_OPACITY] = 1.0;
        cloudy_grid[blocker + LOW_BASE] = 1_000.0;
        cloudy_grid[blocker + LOW_TOP] = 3_600.0;
        let mut clear_grid = vec![0.0f32; cell_count * GRID_FIELDS];
        for cell in 0..cell_count {
            clear_grid[cell * GRID_FIELDS + TERRAIN_ELEVATION] = -30_000.0;
        }
        let make_path = |grid: &[f32]| {
            let batch = Batch {
                grid,
                aerosol_profiles: &[],
                time_count: 1,
                cell_count,
                observers: &[],
                samples: &[],
                step_count: 0,
                rows: 1,
                cols: cell_count,
                lat0: 0.0,
                lon0: 0.0,
                spacing: 0.25,
                radius_km: 250.0,
                observer_geometry_templates: None,
            };
            let mut buffers = SkyBuffers::new();
            buffers.begin_layer_cache(cell_count);
            batch.solar_cloud_base_light(
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                55.5,
                0.0,
                MID,
                4_500.0,
                5_500.0,
                0.8,
                0.8,
                0.0,
                -1.9,
                90.0,
                Some(0.0),
                &mut buffers,
            )
        };

        let clear_path = make_path(&clear_grid);
        let cloudy_path = make_path(&cloudy_grid);

        assert!(clear_path.geometry_reachable);
        assert!(cloudy_path.geometry_reachable);
        assert_eq!(clear_path.path_cloud_transmission_before_target_self, 1.0);
        assert!(cloudy_path.path_cloud_transmission_before_target_self < 1.0);
        assert!(cloudy_path.cloud_transmission < clear_path.cloud_transmission);
    }

    #[test]
    fn low_cloud_on_observer_return_path_reduces_upper_cloud_transmission() {
        let cell_count = 3;
        let mut cloudy_grid = vec![0.0f32; cell_count * GRID_FIELDS];
        for cell in 0..cell_count {
            let offset = cell * GRID_FIELDS;
            cloudy_grid[offset + LOW] = 0.8;
            cloudy_grid[offset + LOW_MATERIAL] = 1.0;
            cloudy_grid[offset + LOW_BLOCKER_OPACITY] = 0.9;
            cloudy_grid[offset + LOW_BASE] = 1_000.0;
            cloudy_grid[offset + LOW_TOP] = 3_000.0;
        }
        let clear_grid = vec![0.0f32; cell_count * GRID_FIELDS];
        let aerosol_profiles = vec![0.0f32; cell_count * AEROSOL_PROFILE_LEVELS];
        let make_path = |grid: &[f32]| {
            let batch = Batch {
                grid,
                aerosol_profiles: &aerosol_profiles,
                time_count: 1,
                cell_count,
                observers: &[],
                samples: &[],
                step_count: 0,
                rows: 1,
                cols: cell_count,
                lat0: 0.0,
                lon0: 0.0,
                spacing: 0.25,
                radius_km: 250.0,
                observer_geometry_templates: None,
            };
            let mut buffers = SkyBuffers::new();
            buffers.begin_layer_cache(cell_count);
            batch.observer_cloud_base_aerosol_optical_depth(
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                55.5,
                90.0,
                9_000.0,
                HIGH,
                false,
                &mut buffers,
            )
        };

        let clear_path = make_path(&clear_grid);
        let cloudy_path = make_path(&cloudy_grid);

        assert_eq!(clear_path.cloud_transmission, 1.0);
        assert!(cloudy_path.cloud_transmission < clear_path.cloud_transmission);
        assert!(cloudy_path.cloud_transmission > 0.0);
    }

    #[test]
    fn local_low_cloud_reduces_a_remote_upper_cloud_view() {
        let make_frame = |local_low: f32| {
            let mut grid = vec![0.0f32; 2 * GRID_FIELDS];
            grid[LOW] = local_low;
            grid[LOW_MATERIAL] = 1.0;
            grid[LOW_BLOCKER_OPACITY] = 1.0;
            grid[LOW_BASE] = 300.0;
            grid[LOW_TOP] = 2_500.0;
            let remote = GRID_FIELDS;
            grid[remote + HIGH] = 0.8;
            grid[remote + HIGH_MATERIAL] = 1.0;
            grid[remote + HIGH_BLOCKER_OPACITY] = 0.2;
            grid[remote + HIGH_BASE] = 4_000.0;
            grid[remote + HIGH_TOP] = 8_000.0;
            let observers = [0.0f32, 0.0, 0.0];
            let mut samples = [0.0f32; SAMPLE_FIELDS];
            samples[0] = -0.1;
            samples[1] = 90.0;
            samples[4] = 0.5;
            samples[SAMPLE_LEFT_SECONDS] = 1_800.0;
            samples[SAMPLE_RIGHT_SECONDS] = 1_800.0;
            samples[SAMPLE_VALID] = 1.0;
            let batch = Batch {
                grid: &grid,
                aerosol_profiles: &[],
                time_count: 1,
                cell_count: 2,
                observers: &observers,
                samples: &samples,
                step_count: 1,
                rows: 1,
                cols: 2,
                lat0: 0.0,
                lon0: 0.0,
                spacing: 0.25,
                radius_km: 100.0,
                observer_geometry_templates: None,
            };
            let mut output = vec![0.0f32; OUTPUT_FIELDS];
            let mut buffers = SkyBuffers::new();
            batch.assess_observer_sky_v2(0, &mut output, &mut buffers);
            output
        };

        let clear = make_frame(0.0);
        let blocked = make_frame(1.0);
        println!(
            "clear view={} burn={} high_burn={} low_view={} blocked view={} burn={} high_burn={} low_view={}",
            clear[VIEW_DIAGNOSTIC_OFFSET],
            clear[VIEW_DIAGNOSTIC_OFFSET + 1],
            clear[VIEW_DIAGNOSTIC_OFFSET + 8],
            clear[VIEW_DIAGNOSTIC_OFFSET + 10],
            blocked[VIEW_DIAGNOSTIC_OFFSET],
            blocked[VIEW_DIAGNOSTIC_OFFSET + 1],
            blocked[VIEW_DIAGNOSTIC_OFFSET + 8],
            blocked[VIEW_DIAGNOSTIC_OFFSET + 10],
        );
        assert!(clear[VIEW_DIAGNOSTIC_OFFSET + 8] > 0.0);
        assert!(blocked[VIEW_DIAGNOSTIC_OFFSET + 8] < clear[VIEW_DIAGNOSTIC_OFFSET + 8]);
    }

    #[test]
    fn local_middle_cloud_reduces_a_remote_upper_cloud_view_even_when_unburnable() {
        let make_frame = |local_middle: f32| {
            let mut grid = vec![0.0f32; 3 * GRID_FIELDS];
            grid[MID] = local_middle;
            grid[MID_MATERIAL] = 1.0;
            grid[MID_BLOCKER_OPACITY] = 1.0;
            grid[MID_BASE] = -100.0;
            grid[MID_TOP] = 3_000.0;
            let remote = GRID_FIELDS;
            grid[remote + HIGH] = 0.8;
            grid[remote + HIGH_MATERIAL] = 1.0;
            grid[remote + HIGH_BLOCKER_OPACITY] = 0.2;
            grid[remote + HIGH_BASE] = 4_000.0;
            grid[remote + HIGH_TOP] = 8_000.0;
            let observers = [0.0f32, 0.0, 0.0];
            let mut samples = [0.0f32; SAMPLE_FIELDS];
            samples[0] = -0.1;
            samples[1] = 90.0;
            samples[SAMPLE_VALID] = 1.0;
            let batch = Batch {
                grid: &grid,
                aerosol_profiles: &[],
                time_count: 1,
                cell_count: 3,
                observers: &observers,
                samples: &samples,
                step_count: 1,
                rows: 1,
                cols: 3,
                lat0: 0.0,
                lon0: 0.0,
                spacing: 0.25,
                radius_km: 100.0,
                observer_geometry_templates: None,
            };
            let mut output = vec![0.0f32; OUTPUT_FIELDS];
            let mut buffers = SkyBuffers::new();
            batch.assess_observer_sky_v2(0, &mut output, &mut buffers);
            output
        };

        let clear = make_frame(0.0);
        let blocked = make_frame(1.0);
        assert!(clear[VIEW_DIAGNOSTIC_OFFSET + 8] > 0.0);
        assert!(blocked[VIEW_DIAGNOSTIC_OFFSET + 8] < clear[VIEW_DIAGNOSTIC_OFFSET + 8]);
        assert!(blocked[VIEW_DIAGNOSTIC_OFFSET + 1] < clear[VIEW_DIAGNOSTIC_OFFSET + 1]);
    }

    #[test]
    fn aerosol_column_cache_is_cell_scoped_and_generation_scoped() {
        let cell_count = 2;
        let time_count = 2;
        let mut grid = vec![0.0f32; time_count * cell_count * GRID_FIELDS];
        for time in 0..time_count {
            for cell in 0..cell_count {
                grid[(time * cell_count + cell) * GRID_FIELDS + AEROSOL_AOD550] =
                    0.1 + (time * cell_count + cell) as f32 * 0.1;
            }
        }
        let profile = test_aerosol_profile();
        let aerosol_profiles = [profile.map(|value| value), profile.map(|value| value * 2.0)]
            .into_iter()
            .flatten()
            .chain(
                [
                    profile.map(|value| value * 3.0),
                    profile.map(|value| value * 4.0),
                ]
                .into_iter()
                .flatten(),
            )
            .collect::<Vec<_>>();
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &aerosol_profiles,
            time_count,
            cell_count,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: cell_count,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let horizontal = [(0, 0.75), (1, 0.25), (0, 0.0), (0, 0.0)];
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(cell_count);

        let first = batch.interpolated_aerosol_column(&mut buffers, 0, 1, 0.25, &horizontal);
        let expected_cell0 = batch.temporal_aerosol_column(0, 1, 0.25, 0);
        let expected_cell1 = batch.temporal_aerosol_column(0, 1, 0.25, 1);
        assert_eq!(buffers.aerosol_column_cache.len(), cell_count);
        assert_eq!(buffers.aerosol_column_cache_generation, vec![1, 1]);
        assert_eq!(
            buffers.aerosol_column_cache[0].aod550.to_bits(),
            expected_cell0.aod550.to_bits()
        );
        assert_eq!(
            buffers.aerosol_column_cache[1].aod550.to_bits(),
            expected_cell1.aod550.to_bits()
        );
        assert_eq!(
            first.aod550.to_bits(),
            batch
                .interpolated_with_horizontal(0, 1, 0.25, &horizontal, AEROSOL_AOD550)
                .to_bits()
        );
        for level in 0..AEROSOL_PROFILE_LEVELS {
            let expected = 0.75 * expected_cell0.extinction_m_inv[level]
                + 0.25 * expected_cell1.extinction_m_inv[level];
            assert_eq!(first.extinction_m_inv[level].to_bits(), expected.to_bits());
        }

        buffers.begin_layer_cache(cell_count);
        let second = batch.interpolated_aerosol_column(&mut buffers, 0, 1, 0.75, &horizontal);
        assert_eq!(buffers.aerosol_column_cache_generation, vec![2, 2]);
        assert_ne!(first.aod550.to_bits(), second.aod550.to_bits());
    }

    #[test]
    fn target_extension_factor_ramps_with_distance_from_target() {
        // The exemption is full near the target, ramps linearly, and is gone
        // beyond the far cutoff so distant same-layer cells block normally.
        assert_eq!(target_extension_factor_at_distance(0.0), 0.20);
        assert_eq!(
            target_extension_factor_at_distance(TARGET_SELF_SHIELD_NEAR_KM),
            0.20
        );
        let mid = 0.5 * (TARGET_SELF_SHIELD_NEAR_KM + TARGET_SELF_SHIELD_FAR_KM);
        assert_eq!(target_extension_factor_at_distance(mid), 0.60);
        assert_eq!(
            target_extension_factor_at_distance(TARGET_SELF_SHIELD_FAR_KM),
            1.0
        );
        assert_eq!(target_extension_factor_at_distance(400.0), 1.0);
        assert_eq!(target_extension_factor_at_distance(f32::NAN), 1.0);
    }

    #[test]
    fn solar_path_integrates_the_full_aerosol_profile() {
        let cell_count = 20;
        let mut grid = vec![0.0f32; cell_count * GRID_FIELDS];
        for cell in 0..cell_count {
            grid[cell * GRID_FIELDS + AEROSOL_AOD550] = 0.25;
        }
        let profile = test_aerosol_profile();
        let aerosol_profiles = profile.repeat(cell_count);
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &aerosol_profiles,
            time_count: 1,
            cell_count,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: cell_count,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 600.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(cell_count);
        buffers.audit_enabled = true;
        let light = batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            HIGH,
            4_000.0,
            5_000.0,
            0.5,
            0.2,
            0.0,
            -0.1,
            90.0,
            Some(0.0),
            &mut buffers,
        );

        assert!(light.geometry_reachable);
        assert!(light.aerosol_optical_depth > 0.0);
        assert_eq!(light.path_cloud_transmission_before_target_self, 1.0);
    }

    #[test]
    fn batched_solar_layers_match_individual_light_paths() {
        let cell_count = 40;
        let mut grid = vec![0.0f32; cell_count * GRID_FIELDS];
        for cell in 0..cell_count {
            let offset = cell * GRID_FIELDS;
            grid[offset + HIGH] = 0.8;
            grid[offset + MID] = 0.7;
            grid[offset + LOW] = 0.6;
            grid[offset + HIGH_MATERIAL] = 0.8;
            grid[offset + MID_MATERIAL] = 0.7;
            grid[offset + LOW_MATERIAL] = 0.6;
            grid[offset + HIGH_BLOCKER_OPACITY] = 0.7;
            grid[offset + MID_BLOCKER_OPACITY] = 0.6;
            grid[offset + LOW_BLOCKER_OPACITY] = 0.5;
            grid[offset + HIGH_BASE] = 7_000.0;
            grid[offset + HIGH_TOP] = 9_500.0;
            grid[offset + MID_BASE] = 3_500.0;
            grid[offset + MID_TOP] = 6_000.0;
            grid[offset + LOW_BASE] = 1_200.0;
            grid[offset + LOW_TOP] = 2_800.0;
            grid[offset + TERRAIN_ELEVATION] = -100_000.0;
        }
        let batch = Batch {
            grid: &grid,
            observer_geometry_templates: None,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: cell_count,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 600.0,
        };
        let clouds = [
            CloudLayerSample {
                amount: 0.8,
                opacity: 0.7,
                material_quality: 0.8,
                base_m: 7_000.0,
                top_m: 9_500.0,
            },
            CloudLayerSample {
                amount: 0.7,
                opacity: 0.6,
                material_quality: 0.7,
                base_m: 3_500.0,
                top_m: 6_000.0,
            },
            CloudLayerSample {
                amount: 0.6,
                opacity: 0.5,
                material_quality: 0.6,
                base_m: 1_200.0,
                top_m: 2_800.0,
            },
        ];
        let risks = [0.1, 0.2, 0.3];
        let mut batched_buffers = SkyBuffers::new();
        batched_buffers.begin_layer_cache(cell_count);
        let batched = batch.solar_cloud_base_lights(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            200.0,
            0.0,
            [true; 3],
            clouds,
            risks,
            -5.0,
            90.0,
            None,
            &mut batched_buffers,
        );

        let work = SolarRayWork::from_solar_inputs(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            200.0,
            0.0,
            1.0,
            [true; 3],
            clouds,
            risks,
            -5.0,
            solar_illumination_elevation_deg(-5.0).to_radians(),
            90.0,
            None,
        );
        let mut work_buffers = SkyBuffers::new();
        work_buffers.begin_layer_cache(cell_count);
        let work_lights = work.evaluate_scalar(&batch, &mut work_buffers);
        for layer in 0..3 {
            assert_eq!(
                work_lights[layer].cloud_transmission.to_bits(),
                batched[layer].cloud_transmission.to_bits()
            );
            assert_eq!(
                work_lights[layer]
                    .path_cloud_transmission_before_target_self
                    .to_bits(),
                batched[layer]
                    .path_cloud_transmission_before_target_self
                    .to_bits()
            );
        }

        for layer in 0..3 {
            let mut single_buffers = SkyBuffers::new();
            single_buffers.begin_layer_cache(cell_count);
            let single = batch.solar_cloud_base_light(
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                200.0,
                0.0,
                layer,
                clouds[layer].base_m,
                clouds[layer].top_m,
                clouds[layer].amount,
                clouds[layer].opacity,
                risks[layer],
                -5.0,
                90.0,
                None,
                &mut single_buffers,
            );
            assert_eq!(batched[layer].geometry_reachable, single.geometry_reachable);
            assert_eq!(
                batched[layer].cloud_transmission.to_bits(),
                single.cloud_transmission.to_bits()
            );
            assert_eq!(
                batched[layer]
                    .path_cloud_transmission_before_target_self
                    .to_bits(),
                single.path_cloud_transmission_before_target_self.to_bits()
            );
            assert_eq!(
                batched[layer].aerosol_optical_depth.to_bits(),
                single.aerosol_optical_depth.to_bits()
            );
            assert_eq!(
                batched[layer].target_deck_connected.to_bits(),
                single.target_deck_connected.to_bits()
            );
            assert_eq!(
                batched[layer].target_deck_attenuated_fraction.to_bits(),
                single.target_deck_attenuated_fraction.to_bits()
            );
            assert_eq!(
                batched[layer].cloud_optical_depth.to_bits(),
                single.cloud_optical_depth.to_bits()
            );
            assert_eq!(
                batched[layer].peak_blocking.to_bits(),
                single.peak_blocking.to_bits()
            );
            assert_eq!(
                batched[layer].clear_gap_count.to_bits(),
                single.clear_gap_count.to_bits()
            );
        }
    }

    #[test]
    fn solar_path_template_reuses_targets_inside_a_grid_block() {
        let cell_count = 80;
        let mut grid = vec![0.0f32; cell_count * GRID_FIELDS];
        for cell in 0..cell_count {
            let offset = cell * GRID_FIELDS;
            grid[offset + HIGH] = 0.6;
            grid[offset + HIGH_MATERIAL] = 0.7;
            grid[offset + HIGH_BLOCKER_OPACITY] = 0.2;
            grid[offset + HIGH_BASE] = 7_000.0;
            grid[offset + HIGH_TOP] = 9_500.0;
            grid[offset + TERRAIN_ELEVATION] = -100_000.0;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: cell_count,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 600.0,
            observer_geometry_templates: None,
        };
        let clouds = [
            CloudLayerSample {
                amount: 0.6,
                opacity: 0.2,
                material_quality: 0.7,
                base_m: 7_000.0,
                top_m: 9_500.0,
            },
            CloudLayerSample::default(),
            CloudLayerSample::default(),
        ];
        let mut buffers = SkyBuffers::new();
        buffers.solar_path_template_cache_enabled = true;
        buffers.begin_layer_cache(cell_count);
        let first = batch.solar_cloud_base_lights(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            5.0,
            200.0,
            0.0,
            [true, false, false],
            clouds,
            [0.0; 3],
            -5.0,
            90.0,
            None,
            &mut buffers,
        );
        let second = batch.solar_cloud_base_lights(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            5.25,
            200.0,
            0.0,
            [true, false, false],
            clouds,
            [0.0; 3],
            -5.0,
            90.0,
            None,
            &mut buffers,
        );

        assert_eq!(buffers.solar_path_template_cache.len(), 1);
        assert_eq!(
            first[HIGH].cloud_transmission.to_bits(),
            second[HIGH].cloud_transmission.to_bits()
        );
        assert_eq!(
            first[HIGH].aerosol_optical_depth.to_bits(),
            second[HIGH].aerosol_optical_depth.to_bits()
        );
    }

    #[test]
    fn solar_path_caps_the_rounded_final_step_at_trace_radius() {
        let cols = 37;
        let mut grid = vec![0.0f32; cols * GRID_FIELDS];
        for cell in 0..cols {
            grid[cell * GRID_FIELDS + TERRAIN_ELEVATION] = -100_000.0;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: cols,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 600.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(cols);
        buffers.audit_enabled = true;
        let light = batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            HIGH,
            4_000.0,
            5_000.0,
            0.0,
            0.0,
            0.0,
            -5.0,
            90.0,
            Some(-100_000.0),
            &mut buffers,
        );

        assert!(light.geometry_reachable);
        assert_eq!(light.audit.trace_steps, 37);
        assert!(!light.audit.domain_break);
        assert!(!light.audit.ray_height_break);
    }

    #[test]
    fn cloud_layer_sampling_preserves_signed_asl_boundaries() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH] = 0.8;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.2;
        grid[HIGH_BASE] = -100.0;
        grid[HIGH_TOP] = -50.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(1);
        let cloud =
            batch.cached_horizontal_cloud_layer_sample(&mut buffers, 0, 0, 0.0, 0.0, 0.0, HIGH);

        assert_eq!(cloud.base_m, -100.0);
        assert_eq!(cloud.top_m, -50.0);
    }

    #[test]
    fn solar_ray_destination_matches_great_circle_geometry() {
        let (lat, lon) = target_point(30.0, 110.0, 90.0, 900.0);
        assert!((lat - 29.671025).abs() < 1e-4);
        assert!((lon - 119.325465).abs() < 1e-4);
    }

    #[test]
    fn horizontal_light_path_sampling_is_bilinear_and_continuous() {
        let mut grid = vec![0.0f32; 1 * 4 * GRID_FIELDS];
        for (cell, value) in [0.0f32, 1.0, 2.0, 3.0].into_iter().enumerate() {
            grid[cell * GRID_FIELDS + AEROSOL_AOD550] = value;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 4,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 2,
            cols: 2,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };

        let centre = batch.interpolated_horizontal(0, 0, 0.0, 0.5, 0.5, AEROSOL_AOD550);
        let left = batch.interpolated_horizontal(0, 0, 0.0, 0.5, 0.499, AEROSOL_AOD550);
        let right = batch.interpolated_horizontal(0, 0, 0.0, 0.5, 0.501, AEROSOL_AOD550);
        let horizontal = batch.horizontal_weights(0.5, 0.5);
        let reused = batch.interpolated_with_horizontal(0, 0, 0.0, &horizontal, AEROSOL_AOD550);

        assert!((centre - 1.5).abs() < 1e-6);
        assert!((reused - centre).abs() < 1e-6);
        assert!((right - left).abs() < 0.01);
    }

    #[test]
    fn horizontal_cloud_layer_sampling_blends_cloudy_members_only_for_height() {
        let mut grid = vec![0.0f32; 1 * 4 * GRID_FIELDS];
        for cell in 0..4 {
            grid[cell * GRID_FIELDS + HIGH] = if cell == 0 { 1.0 } else { 0.0 };
            grid[cell * GRID_FIELDS + HIGH_BLOCKER_OPACITY] = 0.8;
            grid[cell * GRID_FIELDS + HIGH_MATERIAL] = 0.7;
            grid[cell * GRID_FIELDS + HIGH_BASE] = 8_000.0;
            grid[cell * GRID_FIELDS + HIGH_TOP] = 10_000.0;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 4,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 2,
            cols: 2,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(4);
        let cloud =
            batch.cached_horizontal_cloud_layer_sample(&mut buffers, 0, 0, 0.0, 0.5, 0.5, 0);

        assert!((cloud.amount - 0.25).abs() < 1e-6);
        assert!((cloud.base_m - 8_000.0).abs() < 1e-6);
        assert!((cloud.top_m - 10_000.0).abs() < 1e-6);
        assert!((cloud.opacity - 0.8).abs() < 1e-6);
    }

    #[test]
    fn family_wind_displacement_respects_east_north_signs() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH_U] = 20.0;
        grid[HIGH_V] = 10.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 30.0,
            lon0: 110.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };

        let departure = batch.wind_displaced_position(0, 30.0, 110.0, 0, 3_600.0, -1.0);
        let arrival = batch.wind_displaced_position(0, 30.0, 110.0, 0, 3_600.0, 1.0);
        assert!(departure.0 < 30.0 && departure.1 < 110.0);
        assert!(arrival.0 > 30.0 && arrival.1 > 110.0);
    }

    #[test]
    fn moving_cloud_is_sampled_from_a_two_by_two_departure_point() {
        let mut grid = vec![0.0f32; 2 * 4 * GRID_FIELDS];
        for time in 0..2 {
            for cell in 0..4 {
                let offset = (time * 4 + cell) * GRID_FIELDS;
                grid[offset + HIGH] = if cell >= 2 { 1.0 } else { 0.0 };
                grid[offset + HIGH_MATERIAL] = 1.0;
                grid[offset + HIGH_BASE] = 8_000.0;
                grid[offset + HIGH_TOP] = 10_000.0;
                grid[offset + HIGH_BLOCKER_OPACITY] = 0.5;
                grid[offset + HIGH_V] = 10.0;
            }
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 2,
            cell_count: 4,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 2,
            cols: 2,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };

        let stationary = batch.semi_lagrangian_cloud_layer_sample(0, 0, 0.0, 0.0, 0.0, 0.5, 0.5, 0);
        let departure =
            batch.semi_lagrangian_cloud_layer_sample(0, 0, 0.0, 3_600.0, 0.0, 0.5, 0.5, 0);
        assert!((stationary.amount - 0.5).abs() < 1e-6);
        assert!(departure.amount < stationary.amount);
    }

    #[test]
    fn cached_motion_matches_direct_sampling_for_uniform_strong_wind_at_non_grid_point() {
        let mut grid = vec![0.0f32; 1 * 16 * GRID_FIELDS];
        for cell in 0..16 {
            let row = cell / 4;
            let col = cell % 4;
            let offset = cell * GRID_FIELDS;
            grid[offset + HIGH] = 0.20 + row as f32 * 0.10 + col as f32 * 0.15;
            grid[offset + HIGH_U] = 20.0;
            grid[offset + HIGH_V] = 20.0;
            grid[offset + HIGH_TARGET_BODY_RISK] = 0.10 + row as f32 * 0.04 + col as f32 * 0.03;
            grid[offset + LOCAL_UPPER_SUPPORT] = 0.20 + row as f32 * 0.03 + col as f32 * 0.02;
            grid[offset + HIGH_MATERIAL] = 0.8;
            grid[offset + HIGH_BLOCKER_OPACITY] = 0.8;
            grid[offset + HIGH_BASE] = 8_000.0;
            grid[offset + HIGH_TOP] = 10_000.0;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 16,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 4,
            cols: 4,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let lat = 1.70;
        let lon = 1.65;
        let direct = batch.semi_lagrangian_cloud_layer_sample(0, 0, 0.0, 600.0, 0.0, lat, lon, 0);
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(batch.cell_count);
        let cached = batch.cached_motion_cloud_layer_sample(
            &mut buffers,
            0,
            0,
            0.0,
            600.0,
            0.0,
            lat,
            lon,
            0,
        );
        assert!(
            (cached.amount - direct.amount).abs() < 1e-5,
            "cached={cached:?} direct={direct:?}"
        );
        assert!(
            (cached.base_m - direct.base_m).abs() < 0.01,
            "cached={cached:?} direct={direct:?}"
        );
        assert!(
            (cached.top_m - direct.top_m).abs() < 0.01,
            "cached={cached:?} direct={direct:?}"
        );
        assert!((cached.opacity - direct.opacity).abs() < 1e-5);
        for field in [HIGH_TARGET_BODY_RISK, LOCAL_UPPER_SUPPORT] {
            let direct_scalar =
                batch.semi_lagrangian_family_scalar(0, 0, 0.0, 600.0, 0.0, lat, lon, 0, field);
            let cached_scalar = batch.cached_motion_family_scalar(
                &mut buffers,
                0,
                0,
                0.0,
                600.0,
                0.0,
                lat,
                lon,
                0,
                field,
            );
            assert!((cached_scalar - direct_scalar).abs() < 1e-5);
            if field == HIGH_TARGET_BODY_RISK {
                assert!(direct_scalar > 0.0, "direct body risk={direct_scalar}");
                assert!(cached_scalar > 0.0, "cached body risk={cached_scalar}");
            }
        }
    }

    #[test]
    fn cached_motion_returns_the_same_clear_boundary_as_direct_sampling() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH] = 1.0;
        grid[HIGH_U] = 0.0;
        grid[HIGH_V] = 100.0;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.8;
        grid[HIGH_BASE] = 8_000.0;
        grid[HIGH_TOP] = 10_000.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let direct = batch.semi_lagrangian_cloud_layer_sample(0, 0, 0.0, 3_600.0, 0.0, 0.0, 0.0, 0);
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(1);
        let cached = batch.cached_motion_cloud_layer_sample(
            &mut buffers,
            0,
            0,
            0.0,
            3_600.0,
            0.0,
            0.0,
            0.0,
            0,
        );
        assert_eq!(cached.amount.to_bits(), direct.amount.to_bits());
        assert_eq!(cached.base_m.to_bits(), direct.base_m.to_bits());
        assert_eq!(cached.top_m.to_bits(), direct.top_m.to_bits());
    }

    #[test]
    fn target_body_risk_uses_the_matching_pressure_family_field() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH] = 0.6;
        grid[MID] = 0.6;
        grid[LOW] = 0.6;
        grid[HIGH_TARGET_BODY_RISK] = 0.23;
        grid[MID_TARGET_BODY_RISK] = 0.41;
        grid[LOW_TARGET_BODY_RISK] = 0.57;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };

        assert_eq!(
            batch.semi_lagrangian_family_scalar(
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0,
                HIGH_TARGET_BODY_RISK,
            ),
            0.23
        );
        assert_eq!(
            batch.semi_lagrangian_family_scalar(
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1,
                MID_TARGET_BODY_RISK,
            ),
            0.41
        );
        assert_eq!(
            batch.semi_lagrangian_family_scalar(
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                2,
                LOW_TARGET_BODY_RISK,
            ),
            0.57
        );
    }

    #[test]
    fn moving_target_body_risk_uses_the_matching_family_trajectory() {
        let mut grid = vec![0.0f32; 16 * GRID_FIELDS];
        for cell in 0..16 {
            let row = cell / 4;
            let col = cell % 4;
            let offset = cell * GRID_FIELDS;
            grid[offset + HIGH] = 0.8;
            grid[offset + MID] = 0.8;
            grid[offset + LOW] = 0.8;
            grid[offset + HIGH_TARGET_BODY_RISK] = 0.20 + row as f32 * 0.01 + col as f32 * 0.02;
            grid[offset + MID_TARGET_BODY_RISK] = 0.32 + row as f32 * 0.018 - col as f32 * 0.011;
            grid[offset + LOW_TARGET_BODY_RISK] = 0.48 - row as f32 * 0.014 + col as f32 * 0.017;
            grid[offset + HIGH_U] = 25.0;
            grid[offset + MID_V] = -25.0;
            grid[offset + LOW_U] = -25.0;
            grid[offset + LOW_V] = 15.0;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 16,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 4,
            cols: 4,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(batch.cell_count);

        let mut direct_values = [0.0f32; 3];
        for (layer, field) in [
            (0, HIGH_TARGET_BODY_RISK),
            (1, MID_TARGET_BODY_RISK),
            (2, LOW_TARGET_BODY_RISK),
        ] {
            let direct =
                batch.semi_lagrangian_family_scalar(0, 0, 0.0, 600.0, 0.0, 1.5, 1.5, layer, field);
            let sampled = batch.cached_motion_family_scalar(
                &mut buffers,
                0,
                0,
                0.0,
                600.0,
                0.0,
                1.5,
                1.5,
                layer,
                field,
            );
            assert!(
                direct > 0.0 && sampled > 0.0,
                "layer={layer} field={field} direct={direct} sampled={sampled}"
            );
            assert!(
                (sampled - direct).abs() < 1e-5,
                "layer={layer} direct={direct} sampled={sampled}"
            );
            direct_values[layer] = direct;
        }
        assert!((direct_values[0] - direct_values[1]).abs() > 1e-3);
        assert!((direct_values[1] - direct_values[2]).abs() > 1e-3);
    }

    #[test]
    fn clear_target_receives_an_upstream_cloud_from_family_carrier_wind() {
        let mut grid = vec![0.0f32; 4 * GRID_FIELDS];
        for cell in 0..4 {
            let offset = cell * GRID_FIELDS;
            grid[offset + HIGH] = if cell == 0 { 1.0 } else { 0.0 };
            grid[offset + HIGH_U] = 10.0;
            grid[offset + HIGH_TARGET_BODY_RISK] = if cell == 0 { 0.8 } else { 0.0 };
            grid[offset + HIGH_MATERIAL] = 1.0;
            grid[offset + HIGH_BASE] = 8_000.0;
            grid[offset + HIGH_TOP] = 10_000.0;
            grid[offset + HIGH_BLOCKER_OPACITY] = 0.5;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 4,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 2,
            cols: 2,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };

        let cloud = batch.semi_lagrangian_cloud_layer_sample(0, 0, 0.0, 3_600.0, 0.0, 0.5, 1.0, 0);
        assert!(cloud.amount > 0.10);
        assert!((cloud.base_m - 8_000.0).abs() < 1e-3);
        assert!((cloud.top_m - 10_000.0).abs() < 1e-3);
        let deep_risk = batch.semi_lagrangian_family_scalar(
            0,
            0,
            0.0,
            3_600.0,
            0.0,
            0.5,
            1.0,
            0,
            HIGH_TARGET_BODY_RISK,
        );
        assert!(deep_risk > 0.05);
    }

    #[test]
    fn departure_outside_cloud_grid_returns_clear_instead_of_edge_cloud() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH] = 1.0;
        grid[HIGH_BASE] = 8_000.0;
        grid[HIGH_TOP] = 10_000.0;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.8;
        grid[HIGH_V] = 100.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };

        // A northward carrier at the southern grid edge has a departure
        // several degrees south of the domain. The displaced sample clamps to
        // the grid edge so an advected boundary deck stays visible instead of
        // silently reporting clear sky (the old fail-open undercounted
        // blocking near the domain edge).
        let cloud = batch.semi_lagrangian_cloud_layer_sample(0, 0, 0.0, 3_600.0, 0.0, 0.0, 0.0, 0);
        assert_eq!(cloud.amount.to_bits(), 1.0f32.to_bits());
        assert_eq!(cloud.base_m.to_bits(), 8_000.0f32.to_bits());
        assert_eq!(cloud.top_m.to_bits(), 10_000.0f32.to_bits());
    }

    #[test]
    fn zero_wind_and_zero_seconds_preserve_linear_layer_bits() {
        let mut grid = vec![0.0f32; 2 * GRID_FIELDS];
        for (time, value) in [(0, 0.20f32), (1, 0.80f32)] {
            let offset = time * GRID_FIELDS;
            grid[offset + HIGH] = value;
            grid[offset + HIGH_MATERIAL] = 0.2 + value;
            grid[offset + HIGH_BLOCKER_OPACITY] = 0.3 + value;
            grid[offset + HIGH_BASE] = 7_000.0 + value * 100.0;
            grid[offset + HIGH_TOP] = 10_000.0 + value * 100.0;
            grid[offset + PRESSURE] = 300.0;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 2,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 30.0,
            lon0: 110.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let linear = batch.cloud_layer_sample(0, 1, 0.35, 0, 0);
        let transported =
            batch.semi_lagrangian_cloud_layer_sample(0, 1, 0.35, 0.0, 0.0, 30.0, 110.0, 0);
        assert_eq!(linear.amount.to_bits(), transported.amount.to_bits());
    }

    #[test]
    fn clear_left_endpoint_does_not_dilute_right_cloud_geometry() {
        let mut grid = vec![0.0f32; 2 * GRID_FIELDS];
        grid[HIGH_U] = 10.0;
        let right = GRID_FIELDS;
        grid[right + HIGH] = 1.0;
        grid[right + HIGH_MATERIAL] = 0.9;
        grid[right + HIGH_BLOCKER_OPACITY] = 0.8;
        grid[right + HIGH_BASE] = 8_000.0;
        grid[right + HIGH_TOP] = 10_000.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 2,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 30.0,
            lon0: 110.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };

        let cloud =
            batch.semi_lagrangian_cloud_layer_sample(0, 1, 0.5, 3_600.0, 0.0, 30.0, 110.0, 0);
        // Duty-cycle semantics: with one clear and one cloudy bracket side the
        // sample is binary per cell (either the full right deck or clear), so
        // the amount is never diluted to the linear 0.5 half-wall and the
        // chosen side keeps its own geometry.
        assert!(
            (cloud.amount - 1.0).abs() < 1e-6 || cloud.amount.abs() < 1e-6,
            "amount = {}",
            cloud.amount
        );
        if cloud.amount > 0.5 {
            assert!((cloud.base_m - 8_000.0).abs() < 1e-3);
            assert!((cloud.top_m - 10_000.0).abs() < 1e-3);
            assert!((cloud.opacity - 0.8).abs() < 1e-6);
            assert!((cloud.material_quality - 0.9).abs() < 1e-6);
        }

        // Direct dither boundaries: below the threshold keeps the clear side,
        // at/above it selects the full deck.
        let clear = CloudLayerSample::default();
        let deck = CloudLayerSample {
            amount: 1.0,
            opacity: 0.8,
            material_quality: 0.9,
            base_m: 8_000.0,
            top_m: 10_000.0,
        };
        let taken = Batch::blend_cloud_layer_samples(clear, deck, 0.5, 0.0);
        assert!((taken.amount - 1.0).abs() < 1e-6);
        assert!((taken.base_m - 8_000.0).abs() < 1e-3);
        let skipped = Batch::blend_cloud_layer_samples(clear, deck, 0.5, 0.999);
        assert!(skipped.amount.abs() < 1e-6);
        // Both sides present: the ordinary amount-weighted blend still applies.
        let both = Batch::blend_cloud_layer_samples(deck, deck, 0.5, 0.5);
        assert!((both.amount - 1.0).abs() < 1e-6);
    }

    #[test]
    fn cloud_layer_sampling_reads_material_for_the_matching_family() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH] = 0.20;
        grid[HIGH_MATERIAL] = 0.11;
        grid[HIGH_BLOCKER_OPACITY] = 0.8;
        grid[HIGH_BASE] = 8_000.0;
        grid[HIGH_TOP] = 10_000.0;
        grid[MID] = 0.30;
        grid[MID_MATERIAL] = 0.37;
        grid[MID_BLOCKER_OPACITY] = 0.7;
        grid[MID_BASE] = 4_000.0;
        grid[MID_TOP] = 6_000.0;
        grid[LOW] = 0.40;
        grid[LOW_MATERIAL] = 0.83;
        grid[LOW_BLOCKER_OPACITY] = 0.6;
        grid[LOW_BASE] = 1_000.0;
        grid[LOW_TOP] = 2_000.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 1.0,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(1);

        let high = batch.cached_cloud_layer_sample(&mut buffers, 0, 0, 0.0, 0, 0);
        let mid = batch.cached_cloud_layer_sample(&mut buffers, 0, 0, 0.0, 0, 1);
        let low = batch.cached_cloud_layer_sample(&mut buffers, 0, 0, 0.0, 0, 2);
        assert!((high.material_quality - 0.11).abs() < 1e-6);
        assert!((mid.material_quality - 0.37).abs() < 1e-6);
        assert!((low.material_quality - 0.83).abs() < 1e-6);
    }

    #[test]
    fn observer_target_deep_risk_is_bilinear_for_light_and_projection() {
        let mut grid = vec![0.0f32; 4 * GRID_FIELDS];
        let deep_gradient = [0.0f32, 1.0, 1.0, 0.0];
        for (cell, deep_risk) in deep_gradient.into_iter().enumerate() {
            let offset = cell * GRID_FIELDS;
            grid[offset + HIGH] = if cell == 0 { 0.05 } else { 0.0 };
            grid[offset + HIGH_MATERIAL] = 1.0;
            grid[offset + HIGH_BASE] = 5_000.0;
            grid[offset + HIGH_TOP] = 13_000.0;
            grid[offset + HIGH_BLOCKER_OPACITY] = 0.8;
            grid[offset + HIGH_TARGET_BODY_RISK] = deep_risk;
        }

        let run = |observer_lat: f32, observer_lon: f32| {
            let observers = [observer_lat, observer_lon, 0.0f32];
            let mut samples = [0.0f32; SAMPLE_FIELDS];
            samples[0] = -0.1;
            samples[1] = 225.0;
            samples[SAMPLE_VALID] = 1.0;
            let batch = Batch {
                grid: &grid,
                aerosol_profiles: &[],
                time_count: 1,
                cell_count: 4,
                observers: &observers,
                samples: &samples,
                step_count: 1,
                rows: 2,
                cols: 2,
                lat0: 0.0,
                lon0: 0.0,
                spacing: 1.0,
                radius_km: 50.0,
                observer_geometry_templates: None,
            };
            let mut buffers = SkyBuffers::new();
            let mut output = vec![0.0f32; OUTPUT_FIELDS];
            batch.assess_observer_sky_v2(0, &mut output, &mut buffers);
            let target_deep_risk = batch.interpolated_horizontal(
                0,
                0,
                0.0,
                observer_lat,
                observer_lon,
                HIGH_TARGET_BODY_RISK,
            );
            (
                target_deep_risk,
                output[VIEW_DIAGNOSTIC_OFFSET + 23],
                output[VIEW_DIAGNOSTIC_OFFSET + 24],
            )
        };

        let first = run(0.25, 0.25);
        let nearby = run(0.251, 0.25);
        let expected_self_depth = -deep_target_cloud_self_transmission(0.8, first.0).ln();

        assert!((first.0 - 0.375).abs() < 1e-6);
        assert!((first.1 - first.0).abs() < 1e-6);
        assert!(first.2 >= expected_self_depth - 0.05);
        assert!((nearby.0 - first.0).abs() < 0.01);
        assert!((nearby.1 - nearby.0).abs() < 2e-6);
        assert!((nearby.2 - first.2).abs() < 0.05);
    }

    #[test]
    fn remote_cloud_uses_real_top_height_for_angular_coverage() {
        let mut opacity = [0u8; VIEW_ANGULAR_CELLS];
        add_angular_cloud_footprint(
            &mut opacity,
            270.0,
            270.0,
            220.0,
            5000.0,
            7000.0,
            1.0,
            0.25,
            30.0,
        );

        let coverage = angular_cloud_coverage(&opacity);
        let highest_bin = opacity
            .chunks_exact(VIEW_ELEVATION_BINS)
            .flat_map(|row| row.iter().enumerate())
            .filter_map(|(elevation, value)| (*value > 0).then_some(elevation))
            .max()
            .unwrap_or(0);

        assert!(coverage > 0.0 && coverage < 0.55);
        assert!(highest_bin < 35);
    }

    #[test]
    fn high_terrain_column_discards_the_underground_aerosol_share() {
        // A 4 km station must see only the profile share that lives above its
        // terrain. The former terrain-clipped normalization re-scaled the full
        // column AOD onto that band and inflated the above-terrain extinction
        // several-fold over western China.
        let column = AerosolColumn {
            aod550: 0.30,
            extinction_m_inv: test_aerosol_profile(),
        };
        let top = AEROSOL_PROFILE_HEIGHTS_M[AEROSOL_PROFILE_LEVELS - 1];
        let above = column.vertical_optical_depth(4000.0, top, 4000.0);
        let full = column.vertical_optical_depth(AEROSOL_PROFILE_HEIGHTS_M[0], top, 0.0);
        let raw_fraction = column.raw_integral(4000.0, top)
            / column.raw_integral(AEROSOL_PROFILE_HEIGHTS_M[0], top);
        assert!(full == column.aod550 || (full - column.aod550).abs() < 1e-6);
        assert!(above < 0.5 * column.aod550, "above = {above}");
        assert!((above - column.aod550 * raw_fraction).abs() < 1e-5);
    }

    #[test]
    fn full_aerosol_profile_integrates_to_column_aod() {
        let column = AerosolColumn {
            aod550: 0.15,
            extinction_m_inv: test_aerosol_profile(),
        };
        let integrated = column.vertical_optical_depth(
            AEROSOL_PROFILE_HEIGHTS_M[0],
            AEROSOL_PROFILE_HEIGHTS_M[AEROSOL_PROFILE_LEVELS - 1],
            0.0,
        );
        assert!((integrated - column.aod550).abs() < 1e-6);
    }

    #[test]
    fn full_aerosol_profile_single_pass_integral_matches_reference() {
        fn reference(column: &AerosolColumn, lower_asl_m: f32, upper_asl_m: f32) -> f32 {
            if !lower_asl_m.is_finite() || !upper_asl_m.is_finite() || upper_asl_m <= lower_asl_m {
                return 0.0;
            }
            let lower = lower_asl_m.max(AEROSOL_PROFILE_HEIGHTS_M[0]);
            let upper = upper_asl_m.min(AEROSOL_PROFILE_HEIGHTS_M[AEROSOL_PROFILE_LEVELS - 1]);
            if upper <= lower {
                return 0.0;
            }
            let mut integral = 0.0f32;
            let mut segment_lower = lower;
            while segment_lower < upper {
                let segment_upper = AEROSOL_PROFILE_HEIGHTS_M
                    .iter()
                    .copied()
                    .find(|height| *height > segment_lower)
                    .unwrap_or(upper)
                    .min(upper);
                let lower_value = column.raw_value_at_height(segment_lower);
                let upper_value = column.raw_value_at_height(segment_upper);
                integral += 0.5 * (lower_value + upper_value) * (segment_upper - segment_lower);
                segment_lower = segment_upper;
            }
            integral
        }

        let column = AerosolColumn {
            aod550: 0.31,
            extinction_m_inv: test_aerosol_profile(),
        };
        let heights = &*AEROSOL_PROFILE_HEIGHTS_M;
        let cases = [
            (-500.0, 30_000.0),
            (heights[0], heights[10]),
            (heights[2], heights[8]),
            (heights[2] + 17.25, heights[8] - 31.5),
            (1_234.5, 7_654.25),
            (heights[5], heights[5]),
        ];
        for (lower, upper) in cases {
            assert_eq!(
                column.raw_integral(lower, upper).to_bits(),
                reference(&column, lower, upper).to_bits(),
                "integral differs for {lower}..{upper}m"
            );
        }
    }

    #[test]
    fn full_aerosol_profile_normalization_is_invariant_to_extinction_scale() {
        let profile = test_aerosol_profile();
        let scaled = profile.map(|value| value * 7.0);
        let base = AerosolColumn {
            aod550: 0.42,
            extinction_m_inv: profile,
        };
        let scaled_profile = AerosolColumn {
            aod550: 0.42,
            extinction_m_inv: scaled,
        };
        let height = 2_500.0;
        assert!(
            (base.extinction_km_inv(height, 0.0) - scaled_profile.extinction_km_inv(height, 0.0))
                .abs()
                < 1e-7
        );
    }

    #[test]
    fn cached_vertical_normalization_preserves_integral_bits() {
        let column = AerosolColumn {
            aod550: 0.37,
            extinction_m_inv: test_aerosol_profile(),
        };
        let normalization = column.normalization_integral(4_000.0);
        let heights = &*AEROSOL_PROFILE_HEIGHTS_M;
        for (lower, upper, terrain) in [
            (heights[0], heights[10], 0.0),
            (4_000.0, heights[10], 4_000.0),
            (1_234.5, 7_654.25, 1_000.0),
            (-120.0, 1_000.0, -120.0),
            (heights[10], heights[10], 0.0),
        ] {
            assert_eq!(
                column
                    .vertical_optical_depth(lower, upper, terrain)
                    .to_bits(),
                column
                    .vertical_optical_depth_with_normalization(
                        lower,
                        upper,
                        terrain,
                        normalization,
                    )
                    .to_bits(),
                "cached normalization differs for {lower}..{upper} at terrain {terrain}"
            );
        }
    }

    #[test]
    fn observer_cell_vertical_paths_match_single_and_batched_bits() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[TERRAIN_ELEVATION] = 200.0;
        grid[AEROSOL_AOD550] = 0.45;
        let profiles = test_aerosol_profile();
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &profiles,
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 30.0,
            lon0: 110.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let clouds = [
            CloudLayerSample {
                amount: 0.6,
                base_m: 7_000.0,
                top_m: 9_000.0,
                ..CloudLayerSample::default()
            },
            CloudLayerSample {
                amount: 0.5,
                base_m: 4_000.0,
                top_m: 6_000.0,
                ..CloudLayerSample::default()
            },
            CloudLayerSample {
                amount: 0.4,
                base_m: 1_500.0,
                top_m: 3_000.0,
                ..CloudLayerSample::default()
            },
        ];
        let mut single_buffers = SkyBuffers::new();
        single_buffers.begin_layer_cache(1);
        let expected: [ObserverCloudBasePath; 3] = std::array::from_fn(|layer| {
            batch.observer_cloud_base_aerosol_optical_depth(
                0,
                0,
                0.0,
                0.0,
                0.0,
                30.0,
                110.0,
                100.0,
                0.0,
                90.0,
                clouds[layer].base_m,
                layer,
                true,
                &mut single_buffers,
            )
        });
        let mut batched_buffers = SkyBuffers::new();
        batched_buffers.begin_layer_cache(1);
        let actual = batch.observer_cloud_base_aerosol_optical_depths(
            0,
            0,
            0.0,
            0.0,
            0.0,
            30.0,
            110.0,
            100.0,
            0.0,
            90.0,
            clouds,
            true,
            &mut batched_buffers,
        );
        for layer in 0..3 {
            assert_eq!(actual[layer].optical_depth.to_bits(), expected[layer].optical_depth.to_bits());
            assert_eq!(actual[layer].cloud_transmission.to_bits(), expected[layer].cloud_transmission.to_bits());
            assert_eq!(actual[layer].terrain_blocked, expected[layer].terrain_blocked);
        }
    }

    #[test]
    fn full_aerosol_profile_scales_linearly_with_aod() {
        let base = AerosolColumn {
            aod550: 0.21,
            extinction_m_inv: test_aerosol_profile(),
        };
        let doubled = AerosolColumn {
            aod550: 0.42,
            extinction_m_inv: test_aerosol_profile(),
        };
        let height = 2_500.0;
        assert!(
            (doubled.extinction_km_inv(height, 0.0) - 2.0 * base.extinction_km_inv(height, 0.0))
                .abs()
                < 1e-7
        );
    }

    #[test]
    fn scaling_aod_and_profile_together_applies_aod_only_once() {
        let profile = test_aerosol_profile();
        let base = AerosolColumn {
            aod550: 0.20,
            extinction_m_inv: profile,
        };
        let scaled = AerosolColumn {
            aod550: 0.60,
            extinction_m_inv: profile.map(|value| value * 3.0),
        };
        let height = 4_000.0;
        assert!(
            (scaled.extinction_km_inv(height, 0.0) - 3.0 * base.extinction_km_inv(height, 0.0))
                .abs()
                < 1e-7
        );
    }

    #[test]
    fn national_entrypoints_require_the_exact_aerosol_sidecar_shape() {
        let grid = vec![0.0f32; GRID_FIELDS];
        let aerosol_profiles = vec![0.0f32; AEROSOL_PROFILE_LEVELS];
        let observers = vec![0.0f32; OBSERVER_FIELDS];
        let samples = vec![0.0f32; SAMPLE_FIELDS];
        let mut output = vec![0.0f32; OUTPUT_FIELDS];
        assert!(assess_batch(
            &grid,
            &aerosol_profiles,
            AEROSOL_PROFILE_LEVELS,
            1,
            1,
            &observers,
            1,
            &samples,
            1,
            1,
            1,
            1,
            0.0,
            0.0,
            0.25,
            100.0,
            &mut output,
        ));
        assert!(!assess_batch(
            &grid,
            &aerosol_profiles[..AEROSOL_PROFILE_LEVELS - 1],
            AEROSOL_PROFILE_LEVELS,
            1,
            1,
            &observers,
            1,
            &samples,
            1,
            1,
            1,
            1,
            0.0,
            0.0,
            0.25,
            100.0,
            &mut output,
        ));
        assert!(!assess_batch(
            &grid,
            &aerosol_profiles,
            AEROSOL_PROFILE_LEVELS - 1,
            1,
            1,
            &observers,
            1,
            &samples,
            1,
            1,
            1,
            1,
            0.0,
            0.0,
            0.25,
            100.0,
            &mut output,
        ));
    }

    /// Deterministic multi-cell batch fixture for cancellation tests.
    fn cancel_batch_fixture() -> (Vec<f32>, Vec<f32>, Vec<f32>, Vec<f32>, usize, usize) {
        let rows = 16usize;
        let cols = 16usize;
        let cells = rows * cols;
        let times = 2usize;
        let steps = 4usize;
        let observers_count = 64usize;
        let mut grid = vec![0.0f32; times * cells * GRID_FIELDS];
        for time in 0..times {
            for cell in 0..cells {
                let base = (time * cells + cell) * GRID_FIELDS;
                grid[base + HIGH] = 0.4;
                grid[base + HIGH_BASE] = 7_000.0;
                grid[base + HIGH_TOP] = 9_000.0;
                grid[base + TERRAIN_ELEVATION] = 25.0;
            }
        }
        let aerosol = vec![0.0f32; times * cells * AEROSOL_PROFILE_LEVELS];
        let mut observers = vec![0.0f32; observers_count * OBSERVER_FIELDS];
        let mut samples = vec![0.0f32; observers_count * steps * SAMPLE_FIELDS];
        for observer in 0..observers_count {
            let row = observer / cols;
            let col = observer % cols;
            observers[observer * OBSERVER_FIELDS] = row as f32 * 0.25;
            observers[observer * OBSERVER_FIELDS + 1] = 120.0 + col as f32 * 0.25;
            for step in 0..steps {
                let base = (observer * steps + step) * SAMPLE_FIELDS;
                samples[base] = -0.1;
                samples[base + 1] = 265.0;
                samples[base + 2] = 0.0;
                samples[base + 3] = 1.0;
                samples[base + 4] = 0.5;
                samples[base + SAMPLE_VALID] = 1.0;
            }
        }
        (grid, aerosol, observers, samples, cells, steps)
    }

    #[test]
    fn assess_batch_cancel_is_checked_and_uncancelled_batch_completes() {
        let (grid, aerosol, observers, samples, cells, steps) = cancel_batch_fixture();
        let observer_count = observers.len() / OBSERVER_FIELDS;
        let sample_count = samples.len() / SAMPLE_FIELDS;
        let mut output = vec![0.0f32; sample_count * OUTPUT_FIELDS];
        let flag = Arc::new(AtomicBool::new(true));
        let result = assess_batch_with_cancel(
            &grid,
            &aerosol,
            AEROSOL_PROFILE_LEVELS,
            2,
            cells,
            &observers,
            observer_count,
            &samples,
            sample_count,
            steps,
            16,
            16,
            0.0,
            120.0,
            0.25,
            300.0,
            &mut output,
            &BatchCancel::new(flag.clone()),
        );
        assert_eq!(result, Err(NationalAssessmentError::Cancelled));

        // With the flag cleared the same batch must run to completion, so the
        // cancel wiring cannot silently swallow normal work.
        flag.store(false, AtomicOrdering::Relaxed);
        let result = assess_batch_with_cancel(
            &grid,
            &aerosol,
            AEROSOL_PROFILE_LEVELS,
            2,
            cells,
            &observers,
            observer_count,
            &samples,
            sample_count,
            steps,
            16,
            16,
            0.0,
            120.0,
            0.25,
            300.0,
            &mut output,
            &BatchCancel::new(flag.clone()),
        );
        assert_eq!(result, Ok(()));
    }

    #[test]
    fn aerosol_illumination_has_a_logarithmic_visible_response() {
        assert_eq!(fire_cloud_illumination_from_aerosol_tau(0.0), 1.0);
        assert!((fire_cloud_illumination_from_aerosol_tau((50.0f32).ln()) - 0.85).abs() < 1e-6);
        assert!(fire_cloud_illumination_from_aerosol_tau((500.0f32).ln()) >= 0.34);
        assert!(fire_cloud_illumination_from_aerosol_tau((1000.0f32).ln()) > 0.0);
        assert_eq!(
            fire_cloud_illumination_from_aerosol_tau((5000.0f32).ln()),
            0.0,
        );
    }

    #[test]
    fn observer_aerosol_uses_a_contrast_response_separate_from_direct_sunlight() {
        let ordinary_haze_tau = 3.0;
        let near_extinction_tau = (500.0f32).ln();

        assert!(observer_cloud_contrast_from_aerosol_tau(ordinary_haze_tau) > 0.90);
        assert!(observer_cloud_contrast_from_aerosol_tau(ordinary_haze_tau) < 0.94);
        assert!(observer_cloud_contrast_from_aerosol_tau(near_extinction_tau) > 0.70);
        assert!(
            observer_cloud_contrast_from_aerosol_tau((1000.0f32).ln())
                < observer_cloud_contrast_from_aerosol_tau(near_extinction_tau)
        );
        assert_eq!(observer_cloud_contrast_from_aerosol_tau(f32::INFINITY), 0.0);
    }

    #[test]
    fn observer_return_transmission_is_raw_beer_lambert_not_perception() {
        assert_eq!(observer_return_transmission_from_aerosol_tau(0.0), 1.0);
        assert!(
            (observer_return_transmission_from_aerosol_tau(1.0) - (-1.0f32).exp()).abs() < 1e-6
        );
        assert!(
            observer_return_transmission_from_aerosol_tau(3.0)
                < observer_cloud_contrast_from_aerosol_tau(3.0)
        );
        assert_eq!(
            observer_return_transmission_from_aerosol_tau(f32::INFINITY),
            0.0
        );
    }

    #[test]
    fn solar_limb_refraction_decreases_below_the_horizon() {
        let horizon = solar_illumination_elevation_deg(0.0);
        let five_below = solar_illumination_elevation_deg(-5.0);
        assert!((horizon - 0.7486).abs() < 0.001);
        assert!(five_below < -4.6);
        assert!(five_below < -5.0 + 0.833);
    }

    #[test]
    fn local_return_diagnostics_use_a_conditional_visible_mean() {
        assert_eq!(conditional_weighted_mean(0.2, 0.4), 0.5);
        assert_eq!(conditional_weighted_mean(0.2, 0.0), 0.0);
        assert_eq!(conditional_weighted_mean(f32::INFINITY, 0.4), 0.0);
    }

    #[test]
    fn cached_elevation_solid_angle_weights_match_the_original_formula() {
        let weights = &*VIEW_ELEVATION_SOLID_ANGLE_WEIGHTS;
        assert_eq!(weights.len(), VIEW_ELEVATION_BINS);
        for (elevation_bin, actual) in weights.iter().copied().enumerate() {
            let lower = (elevation_bin as f32).to_radians();
            let upper = ((elevation_bin + 1) as f32).to_radians();
            let expected = upper.sin() - lower.sin();
            assert_eq!(actual.to_bits(), expected.to_bits(), "bin {elevation_bin}");
        }
    }

    #[test]
    fn dominant_burnable_pressure_family_handles_threshold_ties_and_multiple_families() {
        assert_eq!(
            dominant_burnable_pressure_family_index([0.20, 0.0, 0.0]),
            Some(0)
        );
        assert_eq!(
            dominant_burnable_pressure_family_index([0.0, 0.20, 0.0]),
            Some(1)
        );
        assert_eq!(
            dominant_burnable_pressure_family_index([0.0, 0.0, 0.20]),
            Some(2)
        );
        assert_eq!(
            dominant_burnable_pressure_family_index([0.20, 0.20, 0.20]),
            Some(0)
        );
        assert_eq!(
            dominant_burnable_pressure_family_index([0.10, 0.30, 0.40]),
            Some(2)
        );
        assert_eq!(
            dominant_burnable_pressure_family_index([0.0049, 0.0048, 0.0047]),
            None
        );
        assert_eq!(
            dominant_burnable_pressure_family_index([0.005, 0.005, 0.005]),
            Some(0)
        );
    }

    #[test]
    fn connected_target_deck_exemption_requires_shallow_non_deep_cloud() {
        assert!(target_deck_can_share_self_shielding(3_999.0, 0.59, 0.19));
        assert!(target_deck_can_share_self_shielding(5_000.0, 0.595, 0.05));
        assert!(target_deck_can_share_self_shielding(6_500.0, 0.60, 0.0));

        assert!(!target_deck_can_share_self_shielding(3_999.0, 0.60, 0.20));
        assert!(!target_deck_can_share_self_shielding(3_999.0, 0.61, 0.19));
        assert!(!target_deck_can_share_self_shielding(6_501.0, 0.79, 0.0));
        assert!(!target_deck_can_share_self_shielding(5_000.0, 0.89, 0.0));
        assert!(!target_deck_can_share_self_shielding(7_300.0, 0.97, 0.0));
    }

    #[test]
    fn layered_upper_target_does_not_inherit_whole_column_deep_risk() {
        let connected_adjacent = CloudLayerSample {
            amount: 0.61,
            base_m: 4_000.0,
            top_m: 6_000.0,
            ..CloudLayerSample::default()
        };
        assert_eq!(
            target_layer_self_shielding_risk(
                1,
                0.42,
                4_200.0,
                6_200.0,
                2_000.0,
                0.51,
                connected_adjacent,
            ),
            0.0
        );
        assert_eq!(
            target_layer_self_shielding_risk(
                0,
                0.42,
                4_200.0,
                6_200.0,
                2_000.0,
                0.51,
                connected_adjacent,
            ),
            0.0
        );
        assert_eq!(
            target_layer_self_shielding_risk(
                1,
                0.42,
                4_200.0,
                11_200.0,
                7_000.0,
                0.51,
                connected_adjacent,
            ),
            0.42
        );
        assert_eq!(
            target_layer_self_shielding_risk(
                1,
                0.42,
                4_200.0,
                6_200.0,
                2_000.0,
                0.51,
                CloudLayerSample {
                    amount: 0.05,
                    ..connected_adjacent
                },
            ),
            0.42
        );
        assert_eq!(
            target_layer_self_shielding_risk(
                2,
                0.42,
                4_200.0,
                6_200.0,
                2_000.0,
                0.51,
                connected_adjacent,
            ),
            0.42
        );
        assert_eq!(
            target_layer_self_shielding_risk(
                1,
                0.42,
                4_200.0,
                6_200.0,
                2_000.0,
                0.85,
                CloudLayerSample {
                    amount: 0.85,
                    ..connected_adjacent
                },
            ),
            0.42
        );
    }

    #[test]
    fn target_self_shielding_gates_match_original_bitwise() {
        fn original(
            layer: usize,
            risk: f32,
            thickness: f32,
            amount: f32,
            adjacent: CloudLayerSample,
        ) -> f32 {
            if layer < 2
                && target_deck_extension_is_connected(adjacent, 4_200.0, 6_200.0)
                && thickness <= MAX_SELF_SHIELD_TARGET_THICKNESS_M
                && amount < MAX_LAYERED_SELF_SHIELD_TARGET_AMOUNT
                && adjacent.amount < MAX_LAYERED_SELF_SHIELD_TARGET_AMOUNT
            {
                0.0
            } else {
                clamp01(risk)
            }
        }

        let nan = f32::from_bits(0x7fc00123);
        let thicknesses = [
            f32::NEG_INFINITY,
            -1.0,
            -0.0,
            0.0,
            f32::from_bits(MAX_SELF_SHIELD_TARGET_THICKNESS_M.to_bits() - 1),
            MAX_SELF_SHIELD_TARGET_THICKNESS_M,
            f32::from_bits(MAX_SELF_SHIELD_TARGET_THICKNESS_M.to_bits() + 1),
            f32::INFINITY,
            nan,
        ];
        let amounts = [
            f32::NEG_INFINITY,
            -0.2,
            -0.0,
            0.0,
            0.05,
            0.1,
            f32::from_bits(MAX_LAYERED_SELF_SHIELD_TARGET_AMOUNT.to_bits() - 1),
            MAX_LAYERED_SELF_SHIELD_TARGET_AMOUNT,
            f32::from_bits(MAX_LAYERED_SELF_SHIELD_TARGET_AMOUNT.to_bits() + 1),
            1.0,
            f32::INFINITY,
            nan,
        ];
        let risks = [-0.5, -0.0, 0.0, 0.19, 0.42, 0.6, 1.0, f32::INFINITY, nan];
        let adjacent_amounts = [
            f32::NEG_INFINITY,
            -0.0,
            0.0,
            f32::from_bits(MIN_CONNECTED_TARGET_DECK_AMOUNT.to_bits() - 1),
            MIN_CONNECTED_TARGET_DECK_AMOUNT,
            f32::from_bits(MIN_CONNECTED_TARGET_DECK_AMOUNT.to_bits() + 1),
            0.61,
            0.75,
            1.0,
            f32::INFINITY,
            nan,
        ];
        let bounds = [
            (4_000.0, 6_000.0),
            (8_000.0, 9_000.0),
            (3_300.0, f32::from_bits(3_300.0f32.to_bits() - 1)),
            (3_300.0, 3_300.0),
            (7_100.0, 8_000.0),
            (f32::from_bits(7_100.0f32.to_bits() + 1), 8_000.0),
            (nan, f32::INFINITY),
            (f32::NEG_INFINITY, nan),
            (f32::NEG_INFINITY, f32::INFINITY),
        ];
        let mut cases = 0;
        for layer in [0, 1, 2, usize::MAX] {
            for thickness in thicknesses {
                for amount in amounts {
                    for risk in risks {
                        for adjacent_amount in adjacent_amounts {
                            for (base_m, top_m) in bounds {
                                let adjacent = CloudLayerSample {
                                    amount: adjacent_amount,
                                    base_m,
                                    top_m,
                                    ..CloudLayerSample::default()
                                };
                                let expected = original(layer, risk, thickness, amount, adjacent);
                                let actual = target_layer_self_shielding_risk(
                                    layer, risk, 4_200.0, 6_200.0, thickness, amount, adjacent,
                                );
                                assert_eq!(actual.to_bits(), expected.to_bits());
                                if !target_layer_self_shielding_needs_adjacent(
                                    layer, thickness, amount,
                                ) {
                                    let skipped = target_layer_self_shielding_risk(
                                        layer,
                                        risk,
                                        4_200.0,
                                        6_200.0,
                                        thickness,
                                        amount,
                                        CloudLayerSample::default(),
                                    );
                                    assert_eq!(skipped.to_bits(), expected.to_bits());
                                }
                                cases += 1;
                            }
                        }
                    }
                }
            }
        }
        assert_eq!(cases, 384_912);
    }

    #[test]
    fn separated_target_keeps_whole_column_deep_risk() {
        let separated_adjacent = CloudLayerSample {
            amount: 0.61,
            base_m: 9_500.0,
            top_m: 11_500.0,
            ..CloudLayerSample::default()
        };

        assert_eq!(
            target_layer_self_shielding_risk(
                1,
                0.42,
                4_200.0,
                6_200.0,
                2_000.0,
                0.51,
                separated_adjacent,
            ),
            0.42
        );
    }

    #[test]
    fn deep_target_cloud_transmits_its_own_sunlight_once() {
        assert_eq!(deep_target_cloud_self_transmission(0.0, 0.45), 1.0);
        assert_eq!(deep_target_cloud_self_transmission(0.55, 0.45), 1.0);
        assert_eq!(deep_target_cloud_self_transmission(0.80, 0.19), 1.0);
        let moderate = deep_target_cloud_self_transmission(0.80, 0.45);
        assert!(moderate < 1.0 && moderate > 0.20);
        assert!((deep_target_cloud_self_transmission(0.80, 0.58) - 0.20).abs() < 1e-6);
        assert!(deep_target_cloud_self_transmission(1.0, 0.45) > 0.02);
    }

    #[test]
    fn deep_target_transmission_does_not_increase_for_thicker_cloud_structure() {
        let moderate = deep_target_cloud_self_transmission(0.80, 0.45);
        let deeper = deep_target_cloud_self_transmission(0.80, 0.60);

        assert!(deeper < moderate);
    }

    #[test]
    fn target_deck_connection_rejects_insignificant_cloud_amount() {
        let weak = CloudLayerSample {
            amount: 0.04,
            base_m: 7_000.0,
            top_m: 9_000.0,
            ..CloudLayerSample::default()
        };
        let significant = CloudLayerSample {
            amount: 0.10,
            ..weak
        };

        assert!(!target_deck_extension_is_connected(weak, 7_200.0, 9_200.0));
        assert!(target_deck_extension_is_connected(
            significant,
            7_200.0,
            9_200.0,
        ));
    }

    #[test]
    fn solar_path_blocking_combines_cloud_amount_and_opacity_once() {
        let cloud = CloudLayerSample {
            amount: 0.25,
            opacity: 0.8,
            ..CloudLayerSample::default()
        };

        assert_eq!(cloud_blocking_factor(cloud, 1.0, 1.0), 0.2);
        assert_eq!(cloud_blocking_factor(cloud, 0.0, 1.0), 0.0);
        assert!((cloud_blocking_factor(cloud, 1.0, 0.20) - 0.04).abs() < 1e-6);
    }

    #[test]
    fn target_self_transmission_is_added_to_optical_depth_before_peak_correction() {
        let target_transmission = 0.20;
        let optical_depth = add_target_self_optical_depth(0.0, target_transmission);
        assert!((optical_depth - (-target_transmission.ln())).abs() < 1e-6);
        assert!(
            (cloud_transmission_from_path(optical_depth, 0.0) - target_transmission).abs() < 1e-6
        );

        // Peak blocking remains a separate unresolved-segment correction.
        assert!(cloud_transmission_from_path(optical_depth, 0.8) < target_transmission);
    }

    #[test]
    fn incoming_aerosol_load_is_separate_from_geometric_burnable_area() {
        let incoming_tau = 3.5;
        let observer_tau = 3.5;
        let light = visible_cloud_base_light(
            SolarCloudBaseLight {
                cloud_transmission: 1.0,
                aerosol_optical_depth: incoming_tau,
                ..SolarCloudBaseLight::default()
            },
            observer_tau,
            1.0,
            CloudLayerSample {
                amount: 0.8,
                opacity: 0.2,
                material_quality: 0.9,
                base_m: 8000.0,
                top_m: 9000.0,
                ..CloudLayerSample::default()
            },
            0,
            100.0,
            12.0,
            -3.0,
        );
        let expected_incoming = fire_cloud_illumination_from_aerosol_tau(incoming_tau);
        let expected_observer = observer_cloud_contrast_from_aerosol_tau(observer_tau);

        assert!((light.incoming_aerosol_illumination - expected_incoming).abs() < 1e-6);
        assert!((light.observer_aerosol_contrast - expected_observer).abs() < 1e-6);
        assert!((light.burnable_fraction - 1.0).abs() < 1e-6);
        assert!((light.observer_burnable_fraction - 1.0).abs() < 1e-6);
    }

    #[test]
    fn near_extinction_does_not_remove_geometric_area_before_load_scoring() {
        let incoming_tau = 1000.0f32.ln();
        let light = visible_cloud_base_light(
            SolarCloudBaseLight {
                cloud_transmission: 1.0,
                aerosol_optical_depth: incoming_tau,
                ..SolarCloudBaseLight::default()
            },
            0.0,
            1.0,
            CloudLayerSample {
                amount: 0.8,
                opacity: 0.2,
                material_quality: 0.9,
                base_m: 8000.0,
                top_m: 9000.0,
                ..CloudLayerSample::default()
            },
            0,
            100.0,
            12.0,
            -3.0,
        );

        // The direct beam is at the tutorial's low-visibility knee, so it
        // must reduce the incoming load. It must not reduce the geometric
        // cloud area here and then be applied again by the scorer.
        assert!(light.incoming_aerosol_illumination <= 0.15);
        assert!((light.burnable_fraction - 1.0).abs() < 1e-6);
        assert!((light.observer_burnable_fraction - 1.0).abs() < 1e-6);
    }

    #[test]
    fn near_low_keeps_observer_return_diagnostics_without_becoming_burnable() {
        let observer_tau = 1.25;
        let light = visible_cloud_base_light(
            SolarCloudBaseLight::default(),
            observer_tau,
            1.0,
            CloudLayerSample {
                amount: 0.8,
                opacity: 0.2,
                material_quality: 0.9,
                base_m: 1_000.0,
                top_m: 2_000.0,
                ..CloudLayerSample::default()
            },
            LOW,
            MIN_REMOTE_LOW_TARGET_DISTANCE_KM - 1.0,
            12.0,
            -3.0,
        );

        assert_eq!(light.burnable_fraction, 0.0);
        assert!(light.incoming_aerosol_illumination > 0.0);
        assert!(light.observer_aerosol_contrast > 0.0);
        assert_eq!(light.observer_aerosol_optical_depth, observer_tau);
        assert_eq!(light.observer_aerosol_transmission, (-observer_tau).exp());
    }

    #[test]
    fn low_cloud_can_remain_a_burnable_target_when_return_path_is_clear() {
        let light = visible_cloud_base_light(
            SolarCloudBaseLight {
                cloud_transmission: 1.0,
                geometry_reachable: true,
                path_cloud_transmission_before_target_self: 1.0,
                ..SolarCloudBaseLight::default()
            },
            0.0,
            1.0,
            CloudLayerSample {
                amount: 0.8,
                opacity: 0.9,
                material_quality: 0.9,
                base_m: 1_000.0,
                top_m: 2_000.0,
                ..CloudLayerSample::default()
            },
            LOW,
            120.0,
            12.0,
            -3.0,
        );

        assert!(light.burnable_fraction > 0.99);
    }

    #[test]
    fn low_cloud_eligibility_has_no_80_km_policy_discontinuity() {
        let external = SolarCloudBaseLight {
            geometry_reachable: true,
            path_cloud_transmission_before_target_self: 1.0,
            ..SolarCloudBaseLight::default()
        };
        let cloud = CloudLayerSample {
            amount: 0.8,
            opacity: 0.2,
            base_m: 1_000.0,
            top_m: 2_000.0,
            ..CloudLayerSample::default()
        };
        let path = ObserverCloudBasePath::default();
        for distance in [79.0, 80.0, 81.0] {
            assert!(low_cloud_target_is_eligible(
                external, path, cloud, 0.0, distance
            ));
        }
        assert!(low_cloud_target_is_eligible(
            external,
            path,
            CloudLayerSample {
                opacity: 100.0,
                ..cloud
            },
            0.0,
            81.0,
        ));
        assert!(!low_cloud_target_is_eligible(
            SolarCloudBaseLight::default(),
            path,
            cloud,
            0.0,
            81.0
        ));
        assert!(!low_cloud_target_is_eligible(
            external,
            ObserverCloudBasePath {
                terrain_blocked: true,
                ..path
            },
            cloud,
            0.0,
            81.0,
        ));
    }

    #[test]
    fn near_low_profile_change_only_updates_observer_return_diagnostics() {
        let mut grid = vec![0.0f32; 2 * GRID_FIELDS];
        grid[LOW] = 0.8;
        grid[LOW_MATERIAL] = 1.0;
        grid[LOW_BLOCKER_OPACITY] = 0.2;
        grid[LOW_BASE] = 1_000.0;
        grid[LOW_TOP] = 2_000.0;
        grid[AEROSOL_AOD550] = 0.5;
        grid[GRID_FIELDS + AEROSOL_AOD550] = 0.5;
        let observers = [0.0f32, 0.25, 0.0];
        let mut samples = [0.0f32; SAMPLE_FIELDS];
        samples[0] = -0.1;
        samples[1] = 270.0;
        samples[SAMPLE_VALID] = 1.0;
        let zero_profile = vec![0.0f32; 2 * AEROSOL_PROFILE_LEVELS];
        let profile = test_aerosol_profile();
        let mut populated_profile = Vec::with_capacity(2 * AEROSOL_PROFILE_LEVELS);
        populated_profile.extend_from_slice(&profile);
        populated_profile.extend_from_slice(&profile);

        let make_frame = |aerosol_profiles: &[f32]| {
            let batch = Batch {
                grid: &grid,
                aerosol_profiles,
                time_count: 1,
                cell_count: 2,
                observers: &observers,
                samples: &samples,
                step_count: 1,
                rows: 1,
                cols: 2,
                lat0: 0.0,
                lon0: 0.0,
                spacing: 0.25,
                radius_km: 100.0,
                observer_geometry_templates: None,
            };
            let mut frame = vec![0.0f32; OUTPUT_FIELDS];
            let mut buffers = SkyBuffers::new();
            batch.assess_observer_sky_v2(0, &mut frame, &mut buffers);
            frame
        };
        let zero_frame = make_frame(&zero_profile);
        let populated_frame = make_frame(&populated_profile);
        let observer_return_offsets = [
            VIEW_DIAGNOSTIC_OFFSET + 30,
            VIEW_DIAGNOSTIC_OFFSET + 31,
            VIEW_DIAGNOSTIC_OFFSET + 32,
            VIEW_DIAGNOSTIC_OFFSET + 33,
        ];

        assert_ne!(
            zero_frame[VIEW_DIAGNOSTIC_OFFSET + 31].to_bits(),
            populated_frame[VIEW_DIAGNOSTIC_OFFSET + 31].to_bits()
        );
        assert_ne!(
            zero_frame[VIEW_DIAGNOSTIC_OFFSET + 2].to_bits(),
            populated_frame[VIEW_DIAGNOSTIC_OFFSET + 2].to_bits()
        );
        assert!(observer_return_offsets
            .iter()
            .any(|index| { zero_frame[*index].to_bits() != populated_frame[*index].to_bits() }));
    }

    #[test]
    fn veto_ledger_is_appended_after_the_existing_output_fields() {
        assert_eq!(
            VETO_CANDIDATE_OFFSET,
            VIEW_DIAGNOSTIC_OFFSET + VIEW_DIAGNOSTIC_FIELDS
        );
        assert_eq!(VETO_TARGET_ELIGIBLE_OFFSET, VETO_CANDIDATE_OFFSET + 1);
        assert_eq!(
            VETO_GEOMETRY_REACHABLE_OFFSET,
            VETO_TARGET_ELIGIBLE_OFFSET + 1
        );
        assert_eq!(
            VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET,
            VETO_GEOMETRY_REACHABLE_OFFSET + 1
        );
        assert_eq!(
            VETO_UNOCCLUDED_BURN_UPPER_OFFSET,
            VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET + 1
        );
        assert_eq!(OUTPUT_FIELDS, VETO_UNOCCLUDED_BURN_UPPER_OFFSET + 1);
    }

    #[test]
    fn solar_ledger_keeps_geometry_for_an_opaque_cloud_path() {
        let mut grid = vec![0.0f32; 2 * GRID_FIELDS];
        grid[HIGH] = 1.0;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 1.0;
        grid[HIGH_BASE] = 4_000.0;
        grid[HIGH_TOP] = 12_000.0;
        let blocker = GRID_FIELDS;
        grid[blocker + HIGH] = 1.0;
        grid[blocker + HIGH_MATERIAL] = 1.0;
        grid[blocker + HIGH_BLOCKER_OPACITY] = 1.0;
        grid[blocker + HIGH_BASE] = 4_000.0;
        grid[blocker + HIGH_TOP] = 12_000.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 2,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 2,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(2);
        buffers.audit_enabled = true;
        let light = batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            HIGH,
            8_000.0,
            10_000.0,
            0.8,
            0.2,
            1.0,
            -0.1,
            90.0,
            Some(0.0),
            &mut buffers,
        );

        assert!(light.geometry_reachable);
        assert_eq!(light.path_cloud_transmission_before_target_self, 0.0);
        assert_eq!(light.cloud_transmission, 0.0);
        assert!(light.audit.opaque_cutoff);
        assert!(light.audit.trace_steps > 0);
    }

    #[test]
    fn solar_path_middle_cloud_blocks_a_remote_upper_target() {
        let mut grid = vec![0.0f32; 3 * GRID_FIELDS];
        grid[HIGH] = 1.0;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.2;
        grid[HIGH_BASE] = 8_000.0;
        grid[HIGH_TOP] = 10_000.0;
        let blocker = GRID_FIELDS;
        grid[blocker + MID] = 1.0;
        grid[blocker + MID_MATERIAL] = 1.0;
        grid[blocker + MID_BLOCKER_OPACITY] = 1.0;
        grid[blocker + MID_BASE] = 7_000.0;
        grid[blocker + MID_TOP] = 9_000.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 3,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 3,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(3);
        buffers.audit_enabled = true;
        let light = batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            HIGH,
            8_000.0,
            10_000.0,
            1.0,
            0.2,
            0.0,
            -0.1,
            90.0,
            Some(0.0),
            &mut buffers,
        );

        assert!(light.geometry_reachable);
        assert_eq!(light.path_cloud_transmission_before_target_self, 0.0);
        assert_eq!(light.cloud_transmission, 0.0);
        assert!(light.audit.opaque_cutoff);
    }

    #[test]
    fn solar_ledger_separates_clear_external_path_from_target_self_transmission() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH] = 1.0;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 1.0;
        grid[HIGH_BASE] = 8_000.0;
        grid[HIGH_TOP] = 10_000.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(1);
        buffers.audit_enabled = true;
        let light = batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            HIGH,
            8_000.0,
            10_000.0,
            1.0,
            1.0,
            1.0,
            -0.1,
            90.0,
            Some(0.0),
            &mut buffers,
        );

        assert!(light.geometry_reachable);
        assert_eq!(light.path_cloud_transmission_before_target_self, 1.0);
        assert!(light.path_cloud_transmission_before_target_self > light.cloud_transmission);
        assert!(light.cloud_transmission > 0.0);
        assert!(light.audit.target_self_tau > 0.0);
        assert!(light.audit.external_tau.abs() < 1e-6);
    }

    #[test]
    fn solar_ledger_marks_ridge_and_negative_ray_as_unreachable_but_domain_exit_clear() {
        let make_batch = |ridge_m: f32| {
            let mut grid = vec![0.0f32; 2 * GRID_FIELDS];
            grid[HIGH] = 0.8;
            grid[HIGH_MATERIAL] = 1.0;
            grid[HIGH_BLOCKER_OPACITY] = 0.2;
            grid[HIGH_BASE] = 8_000.0;
            grid[HIGH_TOP] = 10_000.0;
            grid[GRID_FIELDS + TERRAIN_ELEVATION] = ridge_m;
            grid
        };

        let ridge_grid = make_batch(10_000.0);
        let ridge_batch = Batch {
            grid: &ridge_grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 2,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 2,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut ridge_buffers = SkyBuffers::new();
        ridge_buffers.begin_layer_cache(2);
        let ridge = ridge_batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            HIGH,
            8_000.0,
            10_000.0,
            0.8,
            0.2,
            0.0,
            -0.1,
            90.0,
            Some(0.0),
            &mut ridge_buffers,
        );
        assert!(!ridge.geometry_reachable);
        assert_eq!(ridge.path_cloud_transmission_before_target_self, 0.0);

        let ray_grid = make_batch(0.0);
        let ray_batch = Batch {
            grid: &ray_grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 2,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 2,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut ray_buffers = SkyBuffers::new();
        ray_buffers.begin_layer_cache(2);
        let ray = ray_batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            HIGH,
            8_000.0,
            10_000.0,
            0.8,
            0.2,
            0.0,
            -10.0,
            90.0,
            Some(0.0),
            &mut ray_buffers,
        );
        assert!(!ray.geometry_reachable);

        let domain_grid = make_batch(0.0);
        let domain_batch = Batch {
            grid: &domain_grid[..GRID_FIELDS],
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut domain_buffers = SkyBuffers::new();
        domain_buffers.begin_layer_cache(1);
        let domain_exit = domain_batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            HIGH,
            8_000.0,
            10_000.0,
            0.8,
            0.2,
            0.0,
            -0.1,
            90.0,
            Some(0.0),
            &mut domain_buffers,
        );
        assert!(domain_exit.geometry_reachable);
    }

    #[test]
    fn solar_path_below_sea_level_can_remain_reachable_above_signed_terrain() {
        let cell_count = 20;
        let mut grid = vec![0.0f32; cell_count * GRID_FIELDS];
        grid[HIGH] = 0.8;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.2;
        grid[HIGH_BASE] = 3_400.0;
        grid[HIGH_TOP] = 4_400.0;
        for cell in 0..cell_count {
            grid[cell * GRID_FIELDS + TERRAIN_ELEVATION] = -500.0;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: cell_count,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 600.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(cell_count);
        let light = batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            HIGH,
            3_400.0,
            4_400.0,
            0.8,
            0.2,
            0.0,
            -2.3,
            90.0,
            Some(-500.0),
            &mut buffers,
        );

        assert!(light.geometry_reachable);
    }

    #[test]
    fn solar_path_accepts_negative_cloud_base_above_negative_observer() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH] = 0.8;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.2;
        grid[TERRAIN_ELEVATION] = -500.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };

        for (cloud_base_m, cloud_top_m) in [(-100.0, 100.0), (-100.0, -50.0)] {
            let mut buffers = SkyBuffers::new();
            buffers.begin_layer_cache(1);
            let light = batch.solar_cloud_base_light(
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                HIGH,
                cloud_base_m,
                cloud_top_m,
                0.8,
                0.2,
                0.0,
                -0.1,
                90.0,
                Some(-500.0),
                &mut buffers,
            );

            assert!(
                light.geometry_reachable,
                "signed cloud layer {cloud_base_m}..{cloud_top_m} should be reachable"
            );
        }
    }

    #[test]
    fn observer_target_eligibility_rejects_cloud_base_below_signed_observer() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH] = 0.8;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.2;
        grid[HIGH_BASE] = -600.0;
        grid[HIGH_TOP] = -100.0;
        grid[TERRAIN_ELEVATION] = -500.0;
        let observers = [0.0f32, 0.0, -500.0];
        let mut samples = [0.0f32; SAMPLE_FIELDS];
        samples[0] = -0.1;
        samples[1] = 90.0;
        samples[SAMPLE_VALID] = 1.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &observers,
            samples: &samples,
            step_count: 1,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        let mut output = vec![0.0f32; OUTPUT_FIELDS];
        batch.assess_observer_sky_v2(0, &mut output, &mut buffers);

        assert_eq!(output[VETO_TARGET_ELIGIBLE_OFFSET], 0.0);
        assert_eq!(output[VETO_GEOMETRY_REACHABLE_OFFSET], 0.0);
        assert_eq!(output[VIEW_DIAGNOSTIC_OFFSET + 1], 0.0);
    }

    #[test]
    fn solar_path_over_sunward_downslope_terrain_stays_reachable() {
        // physics-3 regression: the target-cell ground is not a lower bound
        // for the whole sunward ray. A ridge-top cloud at dusk with terrain
        // descending toward the sun must not be terrain-blocked by the target
        // elevation; only the per-node terrain checks decide.
        let cell_count = 24;
        let mut grid = vec![0.0f32; cell_count * GRID_FIELDS];
        grid[HIGH] = 0.8;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.2;
        grid[HIGH_BASE] = 3_000.0;
        grid[HIGH_TOP] = 4_000.0;
        for cell in 0..cell_count {
            // Ridge at the target descending to sea level 60 km sunward.
            let terrain_m = match cell {
                0..=2 => 2_900.0f32,
                3 => 1_933.0,
                4 => 967.0,
                _ => 0.0,
            };
            grid[cell * GRID_FIELDS + TERRAIN_ELEVATION] = terrain_m;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: cell_count,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 700.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(cell_count);
        // Target cell (row 0, col 2); the ray starts there and moves east.
        // At solar_elevation -1.22 deg the illumination slope is about
        // tan(-0.3 deg): the ray dips to ~2915 m at the first node, below the
        // 2900 m target terrain but far above the 1933 m actual terrain.
        let light = batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.5,
            0.0,
            0.0,
            HIGH,
            3_000.0,
            4_000.0,
            0.8,
            0.2,
            0.0,
            -1.22,
            90.0,
            None,
            &mut buffers,
        );

        assert!(light.geometry_reachable);
        assert!(light.cloud_transmission > 0.0);
    }

    #[test]
    fn solar_path_below_signed_terrain_is_blocked() {
        let cell_count = 20;
        let mut grid = vec![0.0f32; cell_count * GRID_FIELDS];
        grid[HIGH] = 0.8;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.2;
        grid[HIGH_BASE] = 3_400.0;
        grid[HIGH_TOP] = 4_400.0;
        for cell in 0..cell_count {
            // The -67 m ray minimum for this fixture must enter a signed
            // below-sea-level ridge at -50 m (plus the 25 m clearance).
            grid[cell * GRID_FIELDS + TERRAIN_ELEVATION] = -50.0;
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 1,
            cols: cell_count,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 600.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache(cell_count);
        let light = batch.solar_cloud_base_light(
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            HIGH,
            3_400.0,
            4_400.0,
            0.8,
            0.2,
            0.0,
            -2.3,
            90.0,
            Some(-500.0),
            &mut buffers,
        );

        assert!(!light.geometry_reachable);
    }

    #[test]
    fn veto_ledger_is_monotonic_and_unoccluded_is_an_upper_bound() {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH] = 0.8;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.2;
        grid[HIGH_BASE] = 8_000.0;
        grid[HIGH_TOP] = 10_000.0;
        let observers = [0.0f32, 0.0, 0.0];
        let mut samples = [0.0f32; SAMPLE_FIELDS];
        samples[0] = -0.1;
        samples[1] = 90.0;
        samples[SAMPLE_VALID] = 1.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &observers,
            samples: &samples,
            step_count: 1,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        let mut output = vec![0.0f32; OUTPUT_FIELDS];
        batch.assess_observer_sky_v2(0, &mut output, &mut buffers);

        let view = output[VIEW_DIAGNOSTIC_OFFSET];
        let burn = output[VIEW_DIAGNOSTIC_OFFSET + 1];
        let candidate = output[VETO_CANDIDATE_OFFSET];
        let eligible = output[VETO_TARGET_ELIGIBLE_OFFSET];
        let geometry = output[VETO_GEOMETRY_REACHABLE_OFFSET];
        let path = output[VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET];
        let upper = output[VETO_UNOCCLUDED_BURN_UPPER_OFFSET];
        // 0.43265393 reflects the 7/6 effective-radius refraction correction
        // in apparent_elevation (previously 0.43275237 with bare geometry).
        assert!((view - 0.43265393).abs() < 1e-6);
        assert!((burn - 0.43265393).abs() < 1e-6);
        assert!(candidate + 1e-6 >= view);
        assert!(view + 1e-6 >= eligible);
        assert!(eligible + 1e-6 >= geometry);
        assert!(geometry + 1e-6 >= path);
        assert!(path + 1e-6 >= burn);
        assert!(upper + 1e-6 >= burn);
        assert!(burn > 0.0);
    }

    #[test]
    fn candidate_ledger_keeps_a_cloud_removed_by_observer_terrain() {
        let mut grid = vec![0.0f32; 3 * GRID_FIELDS];
        let target = 2 * GRID_FIELDS;
        grid[target + HIGH] = 0.8;
        grid[target + HIGH_MATERIAL] = 1.0;
        grid[target + HIGH_BLOCKER_OPACITY] = 0.2;
        grid[target + HIGH_BASE] = 8_000.0;
        grid[target + HIGH_TOP] = 10_000.0;
        grid[GRID_FIELDS + TERRAIN_ELEVATION] = 11_000.0;
        let observers = [0.0f32, 0.0, 0.0];
        let mut samples = [0.0f32; SAMPLE_FIELDS];
        samples[0] = -0.1;
        samples[1] = 90.0;
        samples[SAMPLE_VALID] = 1.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 3,
            observers: &observers,
            samples: &samples,
            step_count: 1,
            rows: 1,
            cols: 3,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        let mut output = vec![0.0f32; OUTPUT_FIELDS];
        batch.assess_observer_sky_v2(0, &mut output, &mut buffers);

        assert!(output[VETO_CANDIDATE_OFFSET] > 0.0);
        assert!(output[VETO_CANDIDATE_OFFSET] >= output[VIEW_DIAGNOSTIC_OFFSET]);
        assert_eq!(output[VIEW_DIAGNOSTIC_OFFSET], 0.0);
        assert_eq!(output[VIEW_DIAGNOSTIC_OFFSET + 1], 0.0);
        assert_eq!(output[VETO_UNOCCLUDED_BURN_UPPER_OFFSET], 0.0);

        // The sidecar must keep the candidate-only terrain projection paired
        // with zero observer burn. This catches diagnostic-slot drift after
        // the target vectors are sorted by distance.
        let mut target_output = vec![0.0f32; TARGET_AUDIT_MAX_RECORDS * TARGET_AUDIT_FIELDS];
        let mut target_frame = vec![0.0f32; OUTPUT_FIELDS];
        let mut target_buffers = SkyBuffers::new();
        batch.assess_observer_sky_v2_with_target_audit(
            0,
            &mut target_frame,
            &mut target_buffers,
            &mut target_output,
        );
        assert_eq!(target_frame, output);
        let valid_records: Vec<&[f32]> = target_output
            .chunks_exact(TARGET_AUDIT_FIELDS)
            .filter(|record| record[TARGET_AUDIT_VALID_OFFSET] > 0.5)
            .collect();
        assert!(!valid_records.is_empty());
        assert!(valid_records.iter().all(|record| {
            let burn = record[TARGET_AUDIT_BURN_CONTRIBUTION_FIELD_OFFSET];
            burn <= record[TARGET_AUDIT_CANDIDATE_OVERLAP_FIELD_OFFSET] + 1e-6
                && (burn <= 1e-6
                    || (record[TARGET_AUDIT_TARGET_ELIGIBLE_OFFSET] > 0.5
                        && record[TARGET_AUDIT_GEOMETRY_REACHABLE_OFFSET] > 0.5))
        }));
    }

    #[test]
    fn path_audit_schema_offsets_are_stable() {
        assert_eq!(PATH_AUDIT_FIELDS, 36);
        assert_eq!(PATH_AUDIT_SCHEMA_VERSION_OFFSET, 0);
        assert_eq!(PATH_AUDIT_EXTERNAL_TAU_OFFSET, 1);
        assert_eq!(PATH_AUDIT_TARGET_SELF_TAU_OFFSET, 2);
        assert_eq!(PATH_AUDIT_PRE_PEAK_EXTERNAL_FIELD_OFFSET, 3);
        assert_eq!(PATH_AUDIT_PEAK_FACTOR_OFFSET, 4);
        assert_eq!(PATH_AUDIT_TRACE_STEPS_MAX_OFFSET, 5);
        assert_eq!(PATH_AUDIT_UNIQUE_CELLS_MAX_OFFSET, 6);
        assert_eq!(PATH_AUDIT_REVISITED_CELLS_MAX_OFFSET, 7);
        assert_eq!(PATH_AUDIT_DOMAIN_BREAK_COUNT_OFFSET, 8);
        assert_eq!(PATH_AUDIT_RAY_HEIGHT_BREAK_COUNT_OFFSET, 9);
        assert_eq!(PATH_AUDIT_OPAQUE_CUTOFF_COUNT_OFFSET, 10);
        assert_eq!(PATH_AUDIT_FAMILY_TAU_HIGH_OFFSET, 11);
        assert_eq!(PATH_AUDIT_FAMILY_TAU_MID_OFFSET, 12);
        assert_eq!(PATH_AUDIT_FAMILY_TAU_LOW_OFFSET, 13);
        assert_eq!(PATH_AUDIT_TARGET_EXTENSION_COUNT_MAX_OFFSET, 14);
        assert_eq!(PATH_AUDIT_VISIBLE_WEIGHT_OFFSET, 15);
        assert_eq!(PATH_AUDIT_PB_LOSS_TOTAL_OFFSET, 16);
        assert_eq!(PATH_AUDIT_PB_DECOMPOSITION_ERROR_OFFSET, 17);
        assert_eq!(PATH_AUDIT_DOMINANT_LOSS_FIELD_OFFSET, 18);
        assert_eq!(PATH_AUDIT_DOMINANT_SHARE_OFFSET, 19);
        assert_eq!(PATH_AUDIT_DOMINANT_VALID_OFFSET, 20);
        assert_eq!(PATH_AUDIT_DOMINANT_TARGET_CELL_INDEX_OFFSET, 21);
        assert_eq!(PATH_AUDIT_DOMINANT_FAMILY_CODE_OFFSET, 22);
        assert_eq!(PATH_AUDIT_DOMINANT_DISTANCE_KM_OFFSET, 23);
        assert_eq!(PATH_AUDIT_DOMINANT_BEARING_DEG_OFFSET, 24);
        assert_eq!(PATH_AUDIT_DOMINANT_AMOUNT_OFFSET, 25);
        assert_eq!(PATH_AUDIT_DOMINANT_BASE_ASL_OFFSET, 26);
        assert_eq!(PATH_AUDIT_DOMINANT_TOP_ASL_OFFSET, 27);
        assert_eq!(PATH_AUDIT_DOMINANT_TERRAIN_ASL_OFFSET, 28);
        assert_eq!(PATH_AUDIT_DOMINANT_MATERIAL_QUALITY_OFFSET, 29);
        assert_eq!(PATH_AUDIT_DOMINANT_TARGET_OPACITY_OFFSET, 30);
        assert_eq!(PATH_AUDIT_DOMINANT_TARGET_DEEP_RISK_OFFSET, 31);
        assert_eq!(PATH_AUDIT_DOMINANT_EFFECTIVE_SELF_TRANSMISSION_OFFSET, 32);
        assert_eq!(PATH_AUDIT_DOMINANT_TARGET_DECK_CONNECTED_OFFSET, 33);
        assert_eq!(PATH_AUDIT_DOMINANT_TARGET_DECK_ATTENUATED_OFFSET, 34);
        assert_eq!(PATH_AUDIT_OMITTED_LOSS_FIELD_OFFSET, 35);
    }

    fn path_audit_fixture() -> (Vec<f32>, Vec<f32>, Vec<f32>) {
        let mut grid = vec![0.0f32; GRID_FIELDS];
        grid[HIGH] = 0.8;
        grid[HIGH_MATERIAL] = 1.0;
        grid[HIGH_BLOCKER_OPACITY] = 0.2;
        grid[HIGH_BASE] = 8_000.0;
        grid[HIGH_TOP] = 10_000.0;
        let observers = vec![0.0f32, 0.0, 0.0];
        let mut samples = vec![0.0f32; SAMPLE_FIELDS];
        samples[0] = -0.1;
        samples[1] = 90.0;
        samples[SAMPLE_VALID] = 1.0;
        (grid, observers, samples)
    }

    #[test]
    fn horizontal_path_cache_reuses_only_target_geometry_and_resets() {
        let batch = Batch {
            grid: &[],
            aerosol_profiles: &[],
            time_count: 0,
            cell_count: 0,
            observers: &[],
            samples: &[],
            step_count: 0,
            rows: 3,
            cols: 3,
            lat0: 30.0,
            lon0: 120.0,
            spacing: 0.25,
            radius_km: 600.0,
            observer_geometry_templates: None,
        };
        let mut buffers = SkyBuffers::new();
        let target =
            batch.cached_horizontal_path_node(&mut buffers, true, 0, 0.0, 30.25, 120.25, 90.0);
        let step =
            batch.cached_horizontal_path_node(&mut buffers, true, 1, 20.0, 30.25, 120.25, 90.0);
        for _ in 0..3 {
            let target_again =
                batch.cached_horizontal_path_node(&mut buffers, true, 0, 0.0, 30.25, 120.25, 90.0);
            let step_again =
                batch.cached_horizontal_path_node(&mut buffers, true, 1, 20.0, 30.25, 120.25, 90.0);
            assert_eq!(target.cell, target_again.cell);
            assert_eq!(target.horizontal, target_again.horizontal);
            assert_eq!(step.distance_km.to_bits(), step_again.distance_km.to_bits());
            assert_eq!(step.cell, step_again.cell);
            assert_eq!(step.horizontal, step_again.horizontal);
        }
        assert_eq!(buffers.solar_horizontal_path.len(), 2);

        buffers.begin_horizontal_path_cache();
        let other_target =
            batch.cached_horizontal_path_node(&mut buffers, true, 0, 0.0, 30.5, 120.25, 90.0);
        assert_eq!(buffers.solar_horizontal_path.len(), 1);
        assert_ne!(target.horizontal, other_target.horizontal);
        let observer_target =
            batch.cached_horizontal_path_node(&mut buffers, false, 0, 0.0, 30.5, 120.25, 270.0);
        assert_eq!(buffers.observer_horizontal_path.len(), 1);
        assert_eq!(observer_target.horizontal, other_target.horizontal);
    }

    #[test]
    fn target_audit_is_separate_and_reuses_the_authoritative_compositor() {
        let (grid, observers, samples) = path_audit_fixture();
        let aerosol_profiles = vec![0.0f32; AEROSOL_PROFILE_LEVELS];
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &aerosol_profiles,
            time_count: 1,
            cell_count: 1,
            observers: &observers,
            samples: &samples,
            step_count: 1,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut normal_frame = [0.0f32; OUTPUT_FIELDS];
        let mut normal_buffers = SkyBuffers::new();
        batch.assess_observer_sky_v2(0, &mut normal_frame, &mut normal_buffers);
        let mut target_frame = [0.0f32; OUTPUT_FIELDS];
        let mut target_buffers = SkyBuffers::new();
        let mut output = vec![0.0f32; TARGET_AUDIT_MAX_RECORDS * TARGET_AUDIT_FIELDS];
        batch.assess_observer_sky_v2_with_target_audit(
            0,
            &mut target_frame,
            &mut target_buffers,
            &mut output,
        );
        assert_eq!(normal_frame, target_frame);
        assert!(audit_selected_targets(
            &grid,
            &aerosol_profiles,
            AEROSOL_PROFILE_LEVELS,
            1,
            1,
            &observers,
            1,
            &samples,
            1,
            1,
            1,
            1,
            0.0,
            0.0,
            0.25,
            100.0,
            &[0],
            &mut output,
        ));
        let first = &output[..TARGET_AUDIT_FIELDS];
        assert_eq!(
            first[TARGET_AUDIT_SCHEMA_OFFSET],
            TARGET_AUDIT_SCHEMA_VERSION
        );
        assert_eq!(first[TARGET_AUDIT_VALID_OFFSET], 1.0);
        assert_eq!(first[TARGET_AUDIT_CELL_INDEX_OFFSET], 0.0);
        assert_eq!(first[TARGET_AUDIT_FAMILY_CODE_OFFSET], 1.0);
        assert!(first[TARGET_AUDIT_CANDIDATE_OVERLAP_FIELD_OFFSET] > 0.0);
        assert!(first[TARGET_AUDIT_VISIBLE_OVERLAP_FIELD_OFFSET] > 0.0);
        assert!(first[TARGET_AUDIT_BURN_CONTRIBUTION_FIELD_OFFSET] > 0.0);
        assert!(first[TARGET_AUDIT_TARGET_ELIGIBLE_OFFSET] > 0.0);
        assert!(first[TARGET_AUDIT_GEOMETRY_REACHABLE_OFFSET] > 0.0);
        assert!(first[TARGET_AUDIT_EXTERNAL_PATH_TRANSMISSION_OFFSET] > 0.0);
        assert!(first[TARGET_AUDIT_TARGET_SELF_TRANSMISSION_OFFSET] > 0.0);
        assert_eq!(first[TARGET_AUDIT_PATH_AUDIT_AVAILABLE_OFFSET], 1.0);
        assert!(output[TARGET_AUDIT_FIELDS + TARGET_AUDIT_VALID_OFFSET..]
            .iter()
            .all(|value| *value == 0.0));
    }

    #[test]
    fn path_audit_preserves_same_solid_angle_chain() {
        let (grid, observers, samples) = path_audit_fixture();
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &observers,
            samples: &samples,
            step_count: 1,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut frame = vec![0.0f32; OUTPUT_FIELDS];
        let mut buffers = SkyBuffers::new();
        let mut audit = PathAuditSummary::default();
        batch.assess_observer_sky_v2_with_audit(0, &mut frame, &mut buffers, &mut audit);
        let mut audit_output = [0.0f32; PATH_AUDIT_FIELDS];
        audit.write_to(&mut audit_output);

        let burn = frame[VIEW_DIAGNOSTIC_OFFSET + 1];
        let geometry = frame[VETO_GEOMETRY_REACHABLE_OFFSET];
        let path = frame[VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET];
        let pre_peak = audit_output[PATH_AUDIT_PRE_PEAK_EXTERNAL_FIELD_OFFSET];
        assert!(burn > 0.0);
        assert!(burn <= path + 1e-6);
        assert!(path <= pre_peak + 1e-6);
        assert!(pre_peak <= geometry + 1e-6);
    }

    #[test]
    fn path_audit_pairs_records_when_distance_sort_reverses_insert_order() {
        // Cell 0 is the farther cloud but is visited first in row-major order;
        // cell 1 is nearer and its solar path crosses cell 0. Their distinct
        // path optical depths make an audit-record swap observable.
        let mut grid = vec![0.0f32; 3 * GRID_FIELDS];
        for (cell, amount) in [(0usize, 1.0f32), (1usize, 0.01f32)] {
            let offset = cell * GRID_FIELDS;
            grid[offset + HIGH] = amount;
            grid[offset + HIGH_MATERIAL] = 1.0;
            grid[offset + HIGH_BLOCKER_OPACITY] = 0.8;
            grid[offset + HIGH_TARGET_BODY_RISK] = 0.9;
            grid[offset + HIGH_BASE] = 8_000.0;
            grid[offset + HIGH_TOP] = 10_000.0;
        }
        let observers = [0.0f32, 0.5, 0.0];
        let mut samples = [0.0f32; SAMPLE_FIELDS];
        samples[0] = -0.1;
        samples[1] = 270.0;
        samples[SAMPLE_VALID] = 1.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 3,
            observers: &observers,
            samples: &samples,
            step_count: 1,
            rows: 1,
            cols: 3,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut frame = vec![0.0f32; OUTPUT_FIELDS];
        let mut buffers = SkyBuffers::new();
        let mut audit = PathAuditSummary::default();
        batch.assess_observer_sky_v2_with_audit(0, &mut frame, &mut buffers, &mut audit);
        let mut audit_output = [0.0f32; PATH_AUDIT_FIELDS];
        audit.write_to(&mut audit_output);

        let burn = frame[VIEW_DIAGNOSTIC_OFFSET + 1];
        let geometry = frame[VETO_GEOMETRY_REACHABLE_OFFSET];
        let path = frame[VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET];
        let pre_peak = audit_output[PATH_AUDIT_PRE_PEAK_EXTERNAL_FIELD_OFFSET];
        assert!(
            pre_peak < 1.0 - 1e-4,
            "Q should include the near cloud path: {pre_peak}"
        );
        assert!(burn <= path + 1e-6, "B={burn} P={path}");
        assert!(path <= pre_peak + 1e-6, "P={path} Q={pre_peak}");
        assert!(pre_peak <= geometry + 1e-6, "Q={pre_peak} G={geometry}");
        assert_eq!(audit_output[PATH_AUDIT_DOMINANT_VALID_OFFSET], 1.0);
        assert!(audit_output[PATH_AUDIT_DOMINANT_LOSS_FIELD_OFFSET] > 0.0);
        assert!(audit_output[PATH_AUDIT_DOMINANT_SHARE_OFFSET] > 0.0);
        assert_eq!(
            audit_output[PATH_AUDIT_DOMINANT_TARGET_CELL_INDEX_OFFSET],
            0.0
        );
        assert_eq!(audit_output[PATH_AUDIT_DOMINANT_FAMILY_CODE_OFFSET], 1.0);
        assert!(audit_output[PATH_AUDIT_DOMINANT_SHARE_OFFSET] > 0.9);
    }

    #[test]
    fn path_audit_allows_illuminated_near_low_cloud_without_a_range_veto() {
        let mut grid = vec![0.0f32; 2 * GRID_FIELDS];
        grid[LOW] = 0.8;
        grid[LOW_MATERIAL] = 1.0;
        grid[LOW_BLOCKER_OPACITY] = 0.2;
        grid[LOW_BASE] = 1_000.0;
        grid[LOW_TOP] = 2_000.0;
        let observers = [0.0f32, 0.25, 0.0];
        let mut samples = [0.0f32; SAMPLE_FIELDS];
        samples[0] = -0.1;
        samples[1] = 270.0;
        samples[SAMPLE_VALID] = 1.0;
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 2,
            observers: &observers,
            samples: &samples,
            step_count: 1,
            rows: 1,
            cols: 2,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut frame = vec![0.0f32; OUTPUT_FIELDS];
        let mut buffers = SkyBuffers::new();
        let mut audit = PathAuditSummary::default();
        batch.assess_observer_sky_v2_with_audit(0, &mut frame, &mut buffers, &mut audit);
        let mut audit_output = [0.0f32; PATH_AUDIT_FIELDS];
        audit.write_to(&mut audit_output);

        let view = frame[VIEW_DIAGNOSTIC_OFFSET];
        let burn = frame[VIEW_DIAGNOSTIC_OFFSET + 1];
        let observer_tau = frame[VIEW_DIAGNOSTIC_OFFSET + 30];
        let observer_transmission = frame[VIEW_DIAGNOSTIC_OFFSET + 31];
        let geometry = frame[VETO_GEOMETRY_REACHABLE_OFFSET];
        let path = frame[VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET];
        let pre_peak = audit_output[PATH_AUDIT_PRE_PEAK_EXTERNAL_FIELD_OFFSET];
        assert!(view > 0.0);
        assert!(burn > 0.0);
        assert_eq!(observer_tau, 0.0);
        assert_eq!(observer_transmission, (-observer_tau).exp());
        assert!(geometry >= burn);
        assert!(path >= burn);
        assert!(pre_peak >= path);
        assert!(audit_output[PATH_AUDIT_PB_LOSS_TOTAL_OFFSET] >= 0.0);
        assert_eq!(audit_output[PATH_AUDIT_DOMINANT_VALID_OFFSET], 0.0);
    }

    #[test]
    fn path_audit_does_not_change_normal_output_bits() {
        let (grid, observers, samples) = path_audit_fixture();
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 1,
            cell_count: 1,
            observers: &observers,
            samples: &samples,
            step_count: 1,
            rows: 1,
            cols: 1,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut before = vec![0.0f32; OUTPUT_FIELDS];
        let mut after = vec![0.0f32; OUTPUT_FIELDS];
        let mut audit_output = [0.0f32; PATH_AUDIT_FIELDS];
        let mut normal_buffers = SkyBuffers::new();
        let mut audit_buffers = SkyBuffers::new();
        let mut final_buffers = SkyBuffers::new();
        let mut audit = PathAuditSummary::default();

        batch.assess_observer_sky_v2(0, &mut before, &mut normal_buffers);
        batch.assess_observer_sky_v2_with_audit(0, &mut after, &mut audit_buffers, &mut audit);
        audit.write_to(&mut audit_output);
        batch.assess_observer_sky_v2(0, &mut after, &mut final_buffers);

        assert_eq!(before, after);
    }

    #[test]
    fn reused_pending_solar_cells_preserve_sample_and_audit_bits() {
        let (mut grid, observers, mut samples) = path_audit_fixture();
        grid.resize(3 * GRID_FIELDS, 0.0);
        let target = 2 * GRID_FIELDS;
        grid[target + MID] = 0.45;
        grid[target + MID_MATERIAL] = 0.8;
        grid[target + MID_BLOCKER_OPACITY] = 0.35;
        grid[target + MID_BASE] = 4_000.0;
        grid[target + MID_TOP] = 5_500.0;
        grid[target + TERRAIN_ELEVATION] = 250.0;
        samples.resize(4 * SAMPLE_FIELDS, 0.0);
        for step in 1..4 {
            let offset = step * SAMPLE_FIELDS;
            samples[offset] = -0.1;
            samples[offset + 1] = 90.0;
            samples[offset + 3] = 1.0;
            samples[offset + 4] = 0.5;
            samples[offset + SAMPLE_VALID] = if step == 2 { 0.0 } else { 1.0 };
        }
        let aerosol_profiles = vec![0.0f32; 3 * AEROSOL_PROFILE_LEVELS];
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &aerosol_profiles,
            time_count: 1,
            cell_count: 3,
            observers: &observers,
            samples: &samples,
            step_count: 4,
            rows: 1,
            cols: 3,
            lat0: 0.0,
            lon0: 0.0,
            spacing: 0.25,
            radius_km: 100.0,
            observer_geometry_templates: None,
        };
        let mut expected = vec![vec![0.0f32; OUTPUT_FIELDS]; 4];
        let mut expected_audit = [0.0f32; PATH_AUDIT_FIELDS];
        let mut expected_targets = vec![0.0f32; TARGET_AUDIT_MAX_RECORDS * TARGET_AUDIT_FIELDS];
        for step in [0, 1, 3] {
            let mut buffers = SkyBuffers::new();
            batch.assess_observer_sky_v2(step, &mut expected[step], &mut buffers);
        }
        let mut audit_buffers = SkyBuffers::new();
        let mut audit_frame = vec![0.0f32; OUTPUT_FIELDS];
        let mut audit = PathAuditSummary::default();
        batch.assess_observer_sky_v2_with_audit(
            0,
            &mut audit_frame,
            &mut audit_buffers,
            &mut audit,
        );
        audit.write_to(&mut expected_audit);
        let mut target_buffers = SkyBuffers::new();
        let mut target_frame = vec![0.0f32; OUTPUT_FIELDS];
        batch.assess_observer_sky_v2_with_target_audit(
            3,
            &mut target_frame,
            &mut target_buffers,
            &mut expected_targets,
        );

        let mut reused = SkyBuffers::new();
        let mut actual = vec![0.0f32; OUTPUT_FIELDS];
        for step in 0..4 {
            batch.assess_observer_sky_v2(step, &mut actual, &mut reused);
            assert_eq!(actual, expected[step], "sample {step}");
            assert!(reused.pending_solar_cells.capacity() > 0 || step == 2);
        }
        let capacity = reused.pending_solar_cells.capacity();
        let mut audit = PathAuditSummary::default();
        batch.assess_observer_sky_v2_with_audit(0, &mut actual, &mut reused, &mut audit);
        assert_eq!(actual, audit_frame);
        let mut audit_output = [0.0f32; PATH_AUDIT_FIELDS];
        audit.write_to(&mut audit_output);
        assert_eq!(audit_output, expected_audit);
        let mut targets = vec![0.0f32; TARGET_AUDIT_MAX_RECORDS * TARGET_AUDIT_FIELDS];
        batch.assess_observer_sky_v2_with_target_audit(3, &mut actual, &mut reused, &mut targets);
        assert_eq!(actual, target_frame);
        assert_eq!(targets, expected_targets);
        assert_eq!(reused.pending_solar_cells.capacity(), capacity);
    }

    #[test]
    fn compact_pending_solar_cell_layout_deduplicates_work_and_geometry() {
        // Layout contract from before the single-source work record. Keep the
        // replica test-only so the production path cannot accidentally regain
        // the duplicated cloud/risk/geometry fields.
        #[derive(Clone, Copy)]
        #[allow(dead_code)]
        struct PreviousPendingSolarCell {
            geometry_index: usize,
            geometry: ObserverCellGeometry,
            cell: usize,
            observer_cell_sample: bool,
            target_lat: f32,
            target_distance: f32,
            target_bearing: f32,
            layers: [CloudLayerSample; 3],
            target_deep_risks: [f32; 3],
            active_solar_layers: [bool; 3],
            solar_work: Option<SolarRayWork>,
        }

        let previous = std::mem::size_of::<PreviousPendingSolarCell>();
        let compact = std::mem::size_of::<PendingSolarCell>();
        assert_eq!((previous, compact), (288, 152));
        assert!(compact <= std::mem::size_of::<SolarRayWork>() + 16);
        eprintln!("PendingSolarCell bytes: previous={previous}, compact={compact}");
    }

    #[test]
    fn compact_pending_solar_cells_cover_masks_terrain_and_reuse() {
        fn case(active_mask: u32, obs_elevation: f32, buffers: &mut SkyBuffers) {
            let cells = 9;
            let mut grid = vec![0.0f32; cells * GRID_FIELDS];
            for cell in 0..cells {
                let offset = cell * GRID_FIELDS;
                for (layer, (amount, opacity, material, base, top, risk)) in [
                    (
                        HIGH,
                        HIGH_BLOCKER_OPACITY,
                        HIGH_MATERIAL,
                        HIGH_BASE,
                        HIGH_TOP,
                        HIGH_TARGET_BODY_RISK,
                    ),
                    (
                        MID,
                        MID_BLOCKER_OPACITY,
                        MID_MATERIAL,
                        MID_BASE,
                        MID_TOP,
                        MID_TARGET_BODY_RISK,
                    ),
                    (
                        LOW,
                        LOW_BLOCKER_OPACITY,
                        LOW_MATERIAL,
                        LOW_BASE,
                        LOW_TOP,
                        LOW_TARGET_BODY_RISK,
                    ),
                ]
                .into_iter()
                .enumerate()
                {
                    let active = active_mask & (1 << layer) != 0;
                    grid[offset + amount] = 0.8;
                    grid[offset + opacity] = 0.2 + layer as f32 * 0.1;
                    grid[offset + material] = 0.7;
                    // Inactive layers remain visible above the observer but
                    // have a below-observer base, excluding solar work while
                    // still exercising the observer-return path.
                    grid[offset + base] = if active {
                        1_200.0 + layer as f32 * 3_000.0
                    } else {
                        obs_elevation - 1_000.0
                    };
                    grid[offset + top] = grid[offset + base].max(obs_elevation) + 1_500.0;
                    grid[offset + risk] = 0.1 + layer as f32 * 0.1;
                }
            }
            let observers = [0.0f32, 0.0, obs_elevation];
            let mut samples = [0.0f32; SAMPLE_FIELDS];
            samples[0] = -0.1;
            samples[1] = 90.0;
            samples[SAMPLE_VALID] = 1.0;
            let aerosol_profiles = vec![0.0f32; cells * AEROSOL_PROFILE_LEVELS];
            let batch = Batch {
                grid: &grid,
                aerosol_profiles: &aerosol_profiles,
                time_count: 1,
                cell_count: cells,
                observers: &observers,
                samples: &samples,
                step_count: 1,
                rows: 3,
                cols: 3,
                lat0: -0.25,
                lon0: -0.25,
                spacing: 0.25,
                radius_km: 100.0,
                observer_geometry_templates: None,
            };

            // Each case replaces the immutable batch input. Invalidate the
            // temporal caches while retaining the pending-vector allocation.
            buffers.begin_layer_cache_with_key(cells, None);
            let mut reference = vec![0.0f32; OUTPUT_FIELDS];
            let mut reference_buffers = SkyBuffers::new();
            batch.assess_observer_sky_v2(0, &mut reference, &mut reference_buffers);
            let mut normal = vec![0.0f32; OUTPUT_FIELDS];
            batch.assess_observer_sky_v2(0, &mut normal, buffers);
            assert_eq!(
                reference
                    .iter()
                    .map(|value| value.to_bits())
                    .collect::<Vec<_>>(),
                normal
                    .iter()
                    .map(|value| value.to_bits())
                    .collect::<Vec<_>>()
            );
            assert!(!buffers.pending_solar_cells.is_empty());
            for pending in &buffers.pending_solar_cells {
                let geometry = buffers.observer_geometry[pending.geometry_index];
                assert_eq!(
                    pending.work.active_mask,
                    active_mask,
                    "cell {} at ({}, {}): {:?}",
                    geometry.cell,
                    geometry.lat,
                    geometry.lon,
                    pending.work.clouds()
                );
                assert_eq!(
                    pending.observer_cell_sample,
                    geometry.cell == buffers.observer_cell
                );
                if pending.observer_cell_sample {
                    assert_eq!(pending.work.target_lat.to_bits(), observers[0].to_bits());
                    assert_eq!(pending.work.target_distance_km.to_bits(), 0.0f32.to_bits());
                    assert_eq!(
                        pending.work.target_terrain_override().map(f32::to_bits),
                        Some(obs_elevation.to_bits())
                    );
                } else {
                    assert_eq!(pending.work.target_lat.to_bits(), geometry.lat.to_bits());
                    assert_eq!(
                        pending.work.target_distance_km.to_bits(),
                        geometry.distance_km.to_bits()
                    );
                    assert_eq!(pending.work.target_terrain_override(), None);
                }
                let clouds = pending.work.clouds();
                for layer in 0..3 {
                    assert_eq!(pending.work.active(layer), active_mask & (1 << layer) != 0);
                    if pending.work.active(layer) {
                        assert!(clouds[layer].base_m > obs_elevation);
                        assert!(clouds[layer].top_m > clouds[layer].base_m);
                    }
                }
            }
            assert_eq!(
                buffers
                    .pending_solar_cells
                    .iter()
                    .filter(|pending| pending.work.active_mask != 0)
                    .count(),
                usize::from(active_mask != 0) * buffers.pending_solar_cells.len()
            );

            let mut audit_frame = vec![0.0f32; OUTPUT_FIELDS];
            let mut audit = PathAuditSummary::default();
            batch.assess_observer_sky_v2_with_audit(0, &mut audit_frame, buffers, &mut audit);
            assert_eq!(
                normal
                    .iter()
                    .map(|value| value.to_bits())
                    .collect::<Vec<_>>(),
                audit_frame
                    .iter()
                    .map(|value| value.to_bits())
                    .collect::<Vec<_>>()
            );
            let mut audit_output = [0.0f32; PATH_AUDIT_FIELDS];
            audit.write_to(&mut audit_output);
            let mut reference_audit_frame = vec![0.0f32; OUTPUT_FIELDS];
            let mut reference_audit = PathAuditSummary::default();
            batch.assess_observer_sky_v2_with_audit(
                0,
                &mut reference_audit_frame,
                &mut reference_buffers,
                &mut reference_audit,
            );
            let mut reference_audit_output = [0.0f32; PATH_AUDIT_FIELDS];
            reference_audit.write_to(&mut reference_audit_output);
            assert_eq!(
                normal
                    .iter()
                    .map(|value| value.to_bits())
                    .collect::<Vec<_>>(),
                reference_audit_frame
                    .iter()
                    .map(|value| value.to_bits())
                    .collect::<Vec<_>>()
            );
            assert_eq!(audit_output, reference_audit_output);
            let mut target_frame = vec![0.0f32; OUTPUT_FIELDS];
            let mut targets = vec![0.0f32; TARGET_AUDIT_MAX_RECORDS * TARGET_AUDIT_FIELDS];
            batch.assess_observer_sky_v2_with_target_audit(
                0,
                &mut target_frame,
                buffers,
                &mut targets,
            );
            assert_eq!(
                normal
                    .iter()
                    .map(|value| value.to_bits())
                    .collect::<Vec<_>>(),
                target_frame
                    .iter()
                    .map(|value| value.to_bits())
                    .collect::<Vec<_>>()
            );
            let mut reference_target_frame = vec![0.0f32; OUTPUT_FIELDS];
            let mut reference_targets =
                vec![0.0f32; TARGET_AUDIT_MAX_RECORDS * TARGET_AUDIT_FIELDS];
            batch.assess_observer_sky_v2_with_target_audit(
                0,
                &mut reference_target_frame,
                &mut reference_buffers,
                &mut reference_targets,
            );
            assert_eq!(
                normal
                    .iter()
                    .map(|value| value.to_bits())
                    .collect::<Vec<_>>(),
                reference_target_frame
                    .iter()
                    .map(|value| value.to_bits())
                    .collect::<Vec<_>>()
            );
            assert_eq!(targets, reference_targets);
        }

        let mut buffers = SkyBuffers::new();
        let mut capacity = 0;
        for active_mask in 0..8 {
            for obs_elevation in [0.0f32, 500.0] {
                case(active_mask, obs_elevation, &mut buffers);
                let next_capacity = buffers.pending_solar_cells.capacity();
                assert!(next_capacity >= capacity);
                capacity = next_capacity;
            }
        }
        assert!(capacity > 0);
    }

    #[test]
    fn direct_shared_motion_builder_matches_the_legacy_table_bitwise() {
        fn cloud_bits(cloud: CloudLayerSample) -> [u32; 5] {
            [
                cloud.amount,
                cloud.opacity,
                cloud.material_quality,
                cloud.base_m,
                cloud.top_m,
            ]
            .map(f32::to_bits)
        }

        let rows = 16;
        let cols = 32;
        let cells = rows * cols;
        let observers = vec![0.0f32; 32 * OBSERVER_FIELDS];
        let mut samples = vec![0.0f32; 32 * 2 * SAMPLE_FIELDS];
        for (index, sample) in samples.chunks_exact_mut(SAMPLE_FIELDS).enumerate() {
            sample[2] = 0.0;
            sample[3] = 1.0;
            sample[4] = 0.37;
            sample[SAMPLE_VALID] = 1.0;
            // Exercise active, zero-wind fallback, and disabled sides while
            // keeping all samples in the same quantized temporal key.
            sample[SAMPLE_LEFT_SECONDS] = if index % 3 == 0 { 0.0 } else { 420.0 };
            sample[SAMPLE_RIGHT_SECONDS] = if index % 5 == 0 { 0.0 } else { 780.0 };
        }
        let mut grid = vec![0.0f32; 2 * cells * GRID_FIELDS];
        for time in 0..2 {
            for cell in 0..cells {
                let offset = (time * cells + cell) * GRID_FIELDS;
                let row = cell / cols;
                let col = cell % cols;
                grid[offset + TERRAIN_ELEVATION] = row as f32 * 25.0;
                for (layer, (amount, opacity, material, base, top, risk, u, v)) in [
                    (
                        HIGH,
                        HIGH_BLOCKER_OPACITY,
                        HIGH_MATERIAL,
                        HIGH_BASE,
                        HIGH_TOP,
                        HIGH_TARGET_BODY_RISK,
                        HIGH_U,
                        HIGH_V,
                    ),
                    (
                        MID,
                        MID_BLOCKER_OPACITY,
                        MID_MATERIAL,
                        MID_BASE,
                        MID_TOP,
                        MID_TARGET_BODY_RISK,
                        MID_U,
                        MID_V,
                    ),
                    (
                        LOW,
                        LOW_BLOCKER_OPACITY,
                        LOW_MATERIAL,
                        LOW_BASE,
                        LOW_TOP,
                        LOW_TARGET_BODY_RISK,
                        LOW_U,
                        LOW_V,
                    ),
                ]
                .into_iter()
                .enumerate()
                {
                    grid[offset + amount] = ((cell + time + layer) % 7) as f32 * 0.12;
                    grid[offset + opacity] = 0.25 + (cell % 5) as f32 * 0.1;
                    grid[offset + material] = 0.6 + (cell % 3) as f32 * 0.1;
                    grid[offset + base] = 1_000.0 + layer as f32 * 3_000.0;
                    grid[offset + top] = grid[offset + base] + 1_200.0;
                    grid[offset + risk] = (row % 4) as f32 * 0.15;
                    grid[offset + LOCAL_UPPER_SUPPORT] = (col % 4) as f32 * 0.1;
                    grid[offset + u] = 8.0 + row as f32;
                    grid[offset + v] = -4.0 + col as f32 * 0.25;
                    if cell % 17 == 0 {
                        grid[offset + u] = 0.0;
                        grid[offset + v] = 0.0;
                    }
                    if cell % 19 == 0 && layer == MID {
                        grid[offset + u] = f32::NAN;
                    }
                }
            }
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 2,
            cell_count: cells,
            observers: &observers,
            samples: &samples,
            step_count: 2,
            rows,
            cols,
            lat0: 20.0,
            lon0: 100.0,
            spacing: 0.25,
            radius_km: 300.0,
            observer_geometry_templates: None,
        };
        let key = temporal_cache_key(0, 1, 0.37, 420.0, 780.0, TemporalCacheMode::Quantized120);
        let weight = f32::from_bits(key.weight_bits);
        let left_seconds = f32::from_bits(key.left_seconds_bits);
        let right_seconds = f32::from_bits(key.right_seconds_bits);
        let mut legacy_layers = vec![[CloudLayerSample::default(); 3]; cells];
        let mut legacy_scalars = vec![[0.0f32; 6]; cells];
        let mut buffers = SkyBuffers::new();
        buffers.begin_layer_cache_for_sample(
            cells,
            key.left,
            key.right,
            weight,
            left_seconds,
            right_seconds,
        );
        for cell in 0..cells {
            let lat = batch.lat0 + (cell / cols) as f32 * batch.spacing;
            let lon = batch.lon0 + (cell % cols) as f32 * batch.spacing;
            for layer in 0..3 {
                legacy_layers[cell][layer] = batch.cached_motion_cloud_layer_sample_for_cell(
                    &mut buffers,
                    key.left,
                    key.right,
                    weight,
                    left_seconds,
                    right_seconds,
                    cell,
                    layer,
                );
                let body_field = match layer {
                    HIGH => HIGH_TARGET_BODY_RISK,
                    MID => MID_TARGET_BODY_RISK,
                    _ => LOW_TARGET_BODY_RISK,
                };
                legacy_scalars[cell][layer * 2] = batch.cached_motion_family_scalar(
                    &mut buffers,
                    key.left,
                    key.right,
                    weight,
                    left_seconds,
                    right_seconds,
                    lat,
                    lon,
                    layer,
                    body_field,
                );
                legacy_scalars[cell][layer * 2 + 1] = batch.cached_motion_family_scalar(
                    &mut buffers,
                    key.left,
                    key.right,
                    weight,
                    left_seconds,
                    right_seconds,
                    lat,
                    lon,
                    layer,
                    LOCAL_UPPER_SUPPORT,
                );
            }
        }
        let cache = batch
            .build_shared_motion_cache(&BatchCancel::default())
            .expect("motion cache");
        let key_index = cache.index(key).expect("motion key");
        assert_eq!(cache.layers[key_index].len(), cells);
        assert_eq!(cache.scalars[key_index].len(), cells);
        for cell in 0..cells {
            for layer in 0..3 {
                assert_eq!(
                    cloud_bits(cache.layers[key_index][cell][layer]),
                    cloud_bits(legacy_layers[cell][layer]),
                    "cell={cell} layer={layer}"
                );
                for slot in 0..2 {
                    assert_eq!(
                        cache.scalars[key_index][cell][layer * 2 + slot].to_bits(),
                        legacy_scalars[cell][layer * 2 + slot].to_bits(),
                        "cell={cell} layer={layer} slot={slot}"
                    );
                }
            }
        }
        let cancelled = BatchCancel::new(Arc::new(AtomicBool::new(true)));
        assert!(batch.build_shared_motion_cache(&cancelled).is_none());
    }

    #[test]
    fn stateless_motion_cell_matches_cached_exact_cell_path() {
        let rows = 4;
        let cols = 8;
        let cells = rows * cols;
        let mut grid = vec![0.0f32; 2 * cells * GRID_FIELDS];
        for time in 0..2 {
            for cell in 0..cells {
                let offset = (time * cells + cell) * GRID_FIELDS;
                for layer in 0..3 {
                    let fields = match layer {
                        HIGH => (
                            HIGH,
                            HIGH_BLOCKER_OPACITY,
                            HIGH_MATERIAL,
                            HIGH_BASE,
                            HIGH_TOP,
                            HIGH_TARGET_BODY_RISK,
                            HIGH_U,
                            HIGH_V,
                        ),
                        MID => (
                            MID,
                            MID_BLOCKER_OPACITY,
                            MID_MATERIAL,
                            MID_BASE,
                            MID_TOP,
                            MID_TARGET_BODY_RISK,
                            MID_U,
                            MID_V,
                        ),
                        _ => (
                            LOW,
                            LOW_BLOCKER_OPACITY,
                            LOW_MATERIAL,
                            LOW_BASE,
                            LOW_TOP,
                            LOW_TARGET_BODY_RISK,
                            LOW_U,
                            LOW_V,
                        ),
                    };
                    grid[offset + fields.0] = 0.2 + ((cell + time + layer) % 5) as f32 * 0.1;
                    grid[offset + fields.1] = 0.6;
                    grid[offset + fields.2] = 0.8;
                    grid[offset + fields.3] = 1_000.0 + layer as f32 * 2_000.0;
                    grid[offset + fields.4] = grid[offset + fields.3] + 1_000.0;
                    grid[offset + fields.5] = cell as f32 * 0.01;
                    grid[offset + LOCAL_UPPER_SUPPORT] = time as f32 * 0.2 + 0.3;
                    grid[offset + fields.6] = 12.0 + cell as f32;
                    grid[offset + fields.7] = -7.0 + time as f32;
                }
            }
        }
        let batch = Batch {
            grid: &grid,
            aerosol_profiles: &[],
            time_count: 2,
            cell_count: cells,
            observers: &[],
            samples: &[],
            step_count: 2,
            rows,
            cols,
            lat0: 20.0,
            lon0: 100.0,
            spacing: 0.25,
            radius_km: 300.0,
            observer_geometry_templates: None,
        };
        // Compare the same operation order: an exact-cell temporal blend in
        // the stateless builder versus the cached exact-cell observer path.
        // Bilinear blending of four temporal blends is intentionally not
        // bitwise equivalent when the one-sided deck dither is nonlinear.
        let cases = [
            (0.0, 0.0, 0.0),
            (0.4, 600.0, 900.0),
            (1.0, 0.0, 1200.0),
            (0.25, 600.0, 0.0),
            (0.75, 0.0, 900.0),
            (0.4, f32::NAN, 900.0),
        ];
        for &(weight, left_seconds, right_seconds) in &cases {
            let mut buffers = SkyBuffers::new();
            buffers.begin_layer_cache_with_key(
                cells,
                Some(temporal_cache_key(
                    0,
                    1,
                    weight,
                    left_seconds,
                    right_seconds,
                    TemporalCacheMode::Exact,
                )),
            );
            for cell in 0..cells {
                let lat = batch.lat0 + (cell / cols) as f32 * batch.spacing;
                let lon = batch.lon0 + (cell % cols) as f32 * batch.spacing;
                let (layers, scalars) =
                    batch.motion_cell_samples(0, 1, weight, left_seconds, right_seconds, cell);
                for layer in 0..3 {
                    let cloud = batch.cached_motion_cloud_layer_sample_for_cell(
                        &mut buffers,
                        0,
                        1,
                        weight,
                        left_seconds,
                        right_seconds,
                        cell,
                        layer,
                    );
                    assert_eq!(
                        [
                            layers[layer].amount,
                            layers[layer].opacity,
                            layers[layer].material_quality,
                            layers[layer].base_m,
                            layers[layer].top_m,
                        ]
                        .map(f32::to_bits),
                        [
                            cloud.amount,
                            cloud.opacity,
                            cloud.material_quality,
                            cloud.base_m,
                            cloud.top_m,
                        ]
                        .map(f32::to_bits),
                        "cell={cell} layer={layer} weight={weight}"
                    );
                    let body_field = match layer {
                        HIGH => HIGH_TARGET_BODY_RISK,
                        MID => MID_TARGET_BODY_RISK,
                        _ => LOW_TARGET_BODY_RISK,
                    };
                    for (slot, field) in [body_field, LOCAL_UPPER_SUPPORT].into_iter().enumerate() {
                        let scalar = batch
                            .cached_motion_family_scalar_for_exact_cell(
                                &mut buffers,
                                0,
                                1,
                                weight,
                                left_seconds,
                                right_seconds,
                                lat,
                                lon,
                                cell,
                                layer,
                                field,
                            )
                            .expect("exact-cell scalar");
                        assert_eq!(
                            scalars[layer * 2 + slot].to_bits(),
                            scalar.to_bits(),
                            "cell={cell} layer={layer} slot={slot} weight={weight}"
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn path_audit_counts_only_nonconsecutive_revisits() {
        let mut trace = SolarPathTrace::default();
        trace.record_cell(3);
        trace.record_cell(3);
        trace.record_cell(4);
        trace.record_cell(3);

        assert_eq!(trace.unique_cells, 2);
        assert_eq!(trace.revisited_cells, 1);
    }

    #[test]
    fn observer_work_reuse_requires_bitwise_equal_inputs() {
        let step_count = 2;
        let mut observers = vec![0.0f32; 3 * OBSERVER_FIELDS];
        observers[0] = 30.0;
        observers[OBSERVER_FIELDS] = 30.0;
        observers[2 * OBSERVER_FIELDS] = 31.0;
        let mut samples = vec![0.0f32; 3 * step_count * SAMPLE_FIELDS];
        for observer_index in 0..3 {
            for step_index in 0..step_count {
                let offset = (observer_index * step_count + step_index) * SAMPLE_FIELDS;
                samples[offset + SAMPLE_VALID] = 1.0;
                samples[offset + 1] = 270.0;
            }
        }

        assert!(observer_work_matches(
            &observers, &samples, 0, 1, step_count
        ));
        assert!(!observer_work_matches(
            &observers, &samples, 0, 2, step_count
        ));
        let canonical = build_observer_work_canonical(&observers, &samples, 3, step_count);
        assert_eq!(canonical, vec![0, 0, 2]);

        observers[OBSERVER_FIELDS] = -0.0;
        assert!(!observer_work_matches(
            &observers, &samples, 0, 1, step_count
        ));
    }
}
