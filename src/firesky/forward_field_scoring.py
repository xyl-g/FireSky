"""Score the complete observer-facing GFS cloud field.

This model does not select a target cloud, rank cells, estimate a camera focal
length, or detect a representative cloud boundary. The native pass projects
every GFS cloud cell in the solar-facing half-disk into the observer's forward
sky. This module turns that angular cloud amount into the 0..2.5 quality scale.
"""

from __future__ import annotations

import os
from typing import Mapping, Sequence

import numpy as np

from firesky.analysis_constants import DEFAULT_ANALYSIS_RADIUS_KM


MAX_SCORE = 2.5
# Final monotonic calibration against saved same-cycle Bot labels. The
# pre-response physical ledger remains separately exposed for diagnosis.
QUALITY_RESPONSE_SCALE = 1.225
QUALITY_RESPONSE_EXPONENT = 1.2
ACTIVE_CLOUD_THRESHOLD = 0.005
# A cloud that occupies the ray-traced field but has no observer-side
# visibility cannot be scored as a visible fire-cloud target.  Keep this as a
# hard physical floor rather than another continuous quality multiplier: the
# native field already contains the cloud amount and the optical corrections
# are applied once below.
MIN_OBSERVER_VISIBILITY_FOR_SCORE = 0.05
NONBURNABLE_BLOCKER_TOLERANCE = 0.015
SCORING_SCOPE = "observer_raytraced_sky_v18"
MATERIAL_REFERENCE_QUALITY = 0.72
OBSERVER_CLOUD_FIELD_SCORE_FORMULA = (
    "physical_ledger -> empirical_response: "
    "perceptual_response(clip(active-period raytraced burnable area * "
    "cloud-material factor * incoming-sunlight AOD illumination * "
    "observer-return AOD contrast, then for active duration >20 min use "
    "the maximum continuous 20-minute pre-response integral within each "
    "active run, then apply the duration factor; native ray-traced burn area "
    "is authoritative and receives no post-raytrace high-cloud area damping, "
    "non-burnable blocker ceiling using only any excess over the native "
    "burnable/view complement, then 0, 2.5))"
)
# Keep the layer boundary explicit in code.  These are names in the flattened
# quality-components payload; the payload deliberately stays flat so existing
# consumers can render it without reconstructing a second score.  A field in
# the physical layer may still be a documented model approximation (for
# example CLWMR/ICMR material or the native path-opacity proxy); this contract
# prevents it from being mistaken for the empirical response layer.
PHYSICAL_LEDGER_COMPONENT_FIELDS = frozenset({
    "forward_view_cloud_field_amount_peak",
    "forward_view_cloud_field_amount_mean",
    "forward_view_cloud_field_amount_effective",
    "forward_view_cloud_field_amount_full_window_mean",
    "forward_burnable_cloud_field_amount_peak",
    "forward_burnable_cloud_field_amount_mean",
    "forward_burnable_cloud_field_amount_effective",
    "forward_burnable_cloud_field_amount_full_window_mean",
    "forward_burnable_cloud_ratio_peak",
    "forward_burnable_cloud_ratio_effective",
    "forward_burnable_cloud_load_peak",
    "forward_burnable_cloud_load_mean",
    "forward_burnable_cloud_load_effective",
    "forward_burnable_cloud_load_full_window_mean",
    "forward_burnable_cloud_material_load_peak",
    "forward_burnable_cloud_material_load_mean",
    "forward_burnable_cloud_material_load_effective",
    "forward_burnable_cloud_material_load_full_window_mean",
    "forward_burnable_cloud_incoming_aerosol_load_mean",
    "forward_burnable_cloud_incoming_aerosol_load_effective",
    "forward_burnable_cloud_incoming_aerosol_load_full_window_mean",
    "burnable_cloud_coverage_peak",
    "burnable_cloud_coverage_mean",
    "burnable_cloud_coverage_effective",
    "burnable_to_view_ratio",
    "cloud_material",
    "quality_aod550",
    "local_aod550",
    "observer_local_cloud_field_amount_peak",
    "observer_local_cloud_field_amount_mean",
    "observer_local_burnable_cloud_field_amount_peak",
    "observer_local_burnable_cloud_field_amount_mean",
    "observer_local_burnable_cloud_load_peak",
    "observer_local_burnable_cloud_load_mean",
    "observer_local_burnable_cloud_material_load_peak",
    "observer_local_burnable_cloud_material_load_mean",
})
EMPIRICAL_RESPONSE_COMPONENT_FIELDS = frozenset({
    "coverage_base_score",
    "coverage_score",
    "material_reference_quality",
    "material_adjustment",
    "material_factor",
    "incoming_aod_illumination_factor",
    "incoming_aod_adjustment",
    "observer_aod_contrast_factor",
    "observer_aod_adjustment",
    "base_quality_index",
    "raw_quality_index",
    "pre_response_quality_index",
    "quality_response_scale",
    "quality_response_exponent",
    "quality_response_adjustment",
    "duration_minutes",
    "duration_factor",
    "duration_window_minutes",
    "duration_window_pre_response_index",
    "duration_adjustment",
    "observer_column_quality_index",
    "observer_column_quality_peak",
    "brightness_color",
})
POLICY_DIAGNOSTIC_COMPONENT_FIELDS = frozenset({
    "scoring_scope",
    "score_formula",
    "score_max",
    "forward_sector_half_angle_deg",
    "forward_field_radius_km",
    "burnable_ratio_factor",
    "solar_incoming_block_risk_peak",
    "solar_incoming_block_risk_mean",
    "solar_incoming_block_risk_effective",
    "observer_column_quality_source",
    "surface_visibility_gate_peak",
    "quality_limiting_factor",
    "physically_burnable_steps",
    "event_active_cloud_threshold",
    "observation_burnability_veto_steps",
    "quality_coverage_source",
})
SCORING_COMPONENT_LAYER_CONTRACT = {
    "physical_ledger": {
        "kind": "physical_or_model_approximation",
        "source": "native-raytraced-ledger-and-gfs-cams-inputs",
        "unit": "field-specific; see component names and cloud optics metadata",
        "fields": PHYSICAL_LEDGER_COMPONENT_FIELDS,
    },
    "empirical_response": {
        "kind": "perceptual_or_bot-label-response",
        "source": "local-monotonic-response-contract",
        "unit": "score-index-or-response-factor",
        "fields": EMPIRICAL_RESPONSE_COMPONENT_FIELDS,
    },
    "policy_diagnostic": {
        "kind": "threshold_configuration_or_non_scoring_diagnostic",
        "source": "scoring-and-native-diagnostic-contract",
        "unit": "field-specific; not an independent score multiplier",
        "fields": POLICY_DIAGNOSTIC_COMPONENT_FIELDS,
    },
}
if (
    PHYSICAL_LEDGER_COMPONENT_FIELDS & EMPIRICAL_RESPONSE_COMPONENT_FIELDS
    or PHYSICAL_LEDGER_COMPONENT_FIELDS & POLICY_DIAGNOSTIC_COMPONENT_FIELDS
    or EMPIRICAL_RESPONSE_COMPONENT_FIELDS & POLICY_DIAGNOSTIC_COMPONENT_FIELDS
):
    raise RuntimeError("scoring component layer contract has overlapping fields")
OBSERVER_CLOUD_FIELD_COMPONENT_FIELDS = frozenset({
    "scoring_scope",
    "score_formula",
    "score_max",
    "forward_sector_half_angle_deg",
    "forward_field_radius_km",
    "forward_view_cloud_field_amount_peak",
    "forward_view_cloud_field_amount_mean",
    "forward_view_cloud_field_amount_effective",
    "forward_view_cloud_field_amount_full_window_mean",
    "forward_burnable_cloud_field_amount_peak",
    "forward_burnable_cloud_field_amount_mean",
    "forward_burnable_cloud_field_amount_effective",
    "forward_burnable_cloud_field_amount_full_window_mean",
    "forward_burnable_cloud_ratio_peak",
    "forward_burnable_cloud_ratio_effective",
    "forward_burnable_cloud_load_peak",
    "forward_burnable_cloud_load_mean",
    "forward_burnable_cloud_load_effective",
    "forward_burnable_cloud_load_full_window_mean",
    "forward_burnable_cloud_material_load_peak",
    "forward_burnable_cloud_material_load_mean",
    "forward_burnable_cloud_material_load_effective",
    "forward_burnable_cloud_material_load_full_window_mean",
    "forward_burnable_cloud_incoming_aerosol_load_mean",
    "forward_burnable_cloud_incoming_aerosol_load_effective",
    "forward_burnable_cloud_incoming_aerosol_load_full_window_mean",
    "burnable_cloud_coverage_peak",
    "burnable_cloud_coverage_mean",
    "burnable_cloud_coverage_effective",
    "burnable_to_view_ratio",
    "coverage_base_score",
    "coverage_score",
    "cloud_material",
    "material_reference_quality",
    "material_factor",
    "material_adjustment",
    "incoming_aod_illumination_factor",
    "incoming_aod_adjustment",
    "observer_aod_contrast_factor",
    "observer_aod_adjustment",
    "burnable_ratio_factor",
    "base_quality_index",
    "raw_quality_index",
    "pre_response_quality_index",
    "quality_response_scale",
    "quality_response_exponent",
    "quality_response_adjustment",
    "duration_minutes",
    "duration_factor",
    "duration_window_minutes",
    "duration_window_pre_response_index",
    "duration_adjustment",
    "quality_aod550",
    "local_aod550",
    "solar_incoming_block_risk_peak",
    "solar_incoming_block_risk_mean",
    "solar_incoming_block_risk_effective",
    "observer_local_cloud_field_amount_peak",
    "observer_local_cloud_field_amount_mean",
    "observer_local_burnable_cloud_field_amount_peak",
    "observer_local_burnable_cloud_field_amount_mean",
    "observer_local_burnable_cloud_load_peak",
    "observer_local_burnable_cloud_load_mean",
    "observer_local_burnable_cloud_material_load_peak",
    "observer_local_burnable_cloud_material_load_mean",
    "observer_column_quality_index",
    "observer_column_quality_peak",
    "observer_column_quality_source",
    "surface_visibility_gate_peak",
    "brightness_color",
    "quality_limiting_factor",
    "physically_burnable_steps",
    "event_active_cloud_threshold",
    "observation_burnability_veto_steps",
    "quality_coverage_source",
})
_SCORING_COMPONENT_LAYER_FIELDS = (
    PHYSICAL_LEDGER_COMPONENT_FIELDS
    | EMPIRICAL_RESPONSE_COMPONENT_FIELDS
    | POLICY_DIAGNOSTIC_COMPONENT_FIELDS
)
if _SCORING_COMPONENT_LAYER_FIELDS != OBSERVER_CLOUD_FIELD_COMPONENT_FIELDS:
    raise RuntimeError(
        "scoring component layer contract does not cover the flattened quality contract"
    )
OBSERVER_CLOUD_FIELD_SCORE_CEILING_COMPONENT_FIELDS = frozenset({
    "observation_score_ceiling",
    "observation_ceiling_adjustment",
})
CLOUD_AMOUNT_SCORE_POINTS = (
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
)


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def calibrated_quality_response(value: np.ndarray | float) -> np.ndarray | float:
    """Map the physical quality ledger to the calibrated perceptual index once."""
    if _use_rust_empirical():
        from firesky.empirical_native import response
        return response(value)
    array = np.asarray(value, dtype=np.float64)
    response = np.clip(
        QUALITY_RESPONSE_SCALE * np.power(np.maximum(array, 0.0), QUALITY_RESPONSE_EXPONENT),
        0.0,
        MAX_SCORE,
    )
    return float(response) if response.ndim == 0 else response


def inverse_calibrated_quality_response(
    value: np.ndarray | float,
) -> np.ndarray | float:
    """Convert a final-index ceiling back to the physical-ledger domain."""
    if _use_rust_empirical():
        from firesky.empirical_native import response
        return response(value, inverse=True)
    array = np.clip(np.asarray(value, dtype=np.float64), 0.0, MAX_SCORE)
    physical = np.clip(
        np.power(array / QUALITY_RESPONSE_SCALE, 1.0 / QUALITY_RESPONSE_EXPONENT),
        0.0,
        MAX_SCORE,
    )
    return float(physical) if physical.ndim == 0 else physical


def _finite_mean(values: Sequence[float] | float, default: float = 0.0) -> float:
    array = np.asarray(values if np.ndim(values) else [values], dtype=np.float64)
    finite = array[np.isfinite(array)]
    return float(np.mean(finite)) if finite.size else default


def _time_mean(values: np.ndarray, offsets: np.ndarray) -> float:
    """Integrate the complete event window for diagnostics.

    Uses the same per-sample support widths (half-step at both ends) as the
    active-period and best-window means. For evenly spaced steps this is
    algebraically identical to the previous trapezoid rule; stating it via
    ``_sample_widths`` keeps one integration convention across the scorer.
    """
    if values.size == 0:
        return 0.0
    widths = _sample_widths(offsets)
    total = float(np.sum(widths))
    if total <= 0.0:
        return float(np.mean(values))
    return float(np.sum(np.asarray(values, dtype=np.float64) * widths) / total)


def _sample_widths(offsets: np.ndarray) -> np.ndarray:
    """Return the time interval represented by every event-window sample."""
    if offsets.size == 0:
        return np.empty(0, dtype=np.float64)
    if offsets.size == 1:
        return np.full(1, 5.0, dtype=np.float64)
    order = np.argsort(offsets)
    ordered_offsets = offsets[order]
    gaps = np.diff(ordered_offsets)
    ordered_widths = np.empty(ordered_offsets.size, dtype=np.float64)
    ordered_widths[0] = gaps[0] * 0.5
    ordered_widths[-1] = gaps[-1] * 0.5
    if ordered_widths.size > 2:
        ordered_widths[1:-1] = 0.5 * (gaps[:-1] + gaps[1:])
    widths = np.empty_like(ordered_widths)
    widths[order] = ordered_widths
    return widths


def _active_time_mean(
    values: np.ndarray,
    offsets: np.ndarray,
    active: np.ndarray,
) -> float:
    """Average event quality only while a physically burnable field exists."""
    if values.size == 0 or not np.any(active):
        return 0.0
    widths = _sample_widths(offsets)
    active_width = float(np.sum(widths[active]))
    if active_width <= 0.0:
        return float(np.mean(values[active]))
    return float(np.sum(values[active] * widths[active]) / active_width)


def _active_duration_minutes(offsets: np.ndarray, active: np.ndarray) -> float:
    """Return the sampled time covered by any non-trivial burnable field."""
    if offsets.size == 0 or not np.any(active):
        return 0.0
    return float(np.sum(_sample_widths(offsets)[active]))


def _event_active_cloud(burn: np.ndarray) -> np.ndarray:
    """Exclude samples below the absolute physical burnability floor.

    The event mask, the event duration and the per-step
    ``physically_burnable`` publication all share this single absolute
    threshold. A relative ``peak * fraction`` rule used to live here, but it
    let one bright sample deactivate an otherwise burnable plateau: the
    plateau vanished from ``duration_minutes`` while the per-step contract
    still published those steps as burnable, and the event was punished
    twice for the same minutes.
    """
    return burn >= ACTIVE_CLOUD_THRESHOLD


def _apply_nonburnable_blocker_consistency_cap(
    view: np.ndarray,
    burn: np.ndarray,
    load: np.ndarray,
    material_load: np.ndarray,
    incoming_aerosol_load: np.ndarray,
    incoming_block: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Cap only burnable area not already explained by the native field."""
    block = np.broadcast_to(
        np.clip(np.asarray(incoming_block, dtype=np.float64), 0.0, 1.0),
        burn.shape,
    )
    ceiling = np.where(
        block > 1e-9,
        np.clip(
            view * (1.0 - block + NONBURNABLE_BLOCKER_TOLERANCE),
            0.0,
            1.0,
        ),
        1.0,
    )
    effective_burn = np.minimum(burn, ceiling)
    scale = np.divide(
        effective_burn,
        burn,
        out=np.ones_like(burn),
        where=burn > 1e-9,
    )
    scale = np.clip(scale, 0.0, 1.0)
    return (
        effective_burn,
        load * scale,
        material_load * scale,
        incoming_aerosol_load * scale,
        scale,
    )


def _piecewise(value: float, points: Sequence[tuple[float, float]]) -> float:
    value = float(value)
    if value <= points[0][0]:
        return float(points[0][1])
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if value <= right_x:
            fraction = _clip((value - left_x) / max(right_x - left_x, 1e-9))
            return float(left_y + fraction * (right_y - left_y))
    return float(points[-1][1])


def score_instant_observer_cloud_field_batch(
    view_amount: np.ndarray,
    burnable_amount: np.ndarray,
    burnable_load: np.ndarray,
    burnable_material_load: np.ndarray,
    burnable_incoming_aerosol_load: np.ndarray,
    vividness: np.ndarray,
    local_aod550: np.ndarray | float,
    burnable_gate: np.ndarray | None = None,
    incoming_block_risk: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Score independent observer-field samples without per-sample Python calls.

    This is the exact ``apply_duration=False`` branch of
    :func:`score_observer_cloud_field`. Event-window aggregation remains in the
    scalar scorer because it depends on each point's active sample widths.
    """
    view = np.clip(np.asarray(view_amount, dtype=np.float64), 0.0, 1.0)
    burn = np.clip(np.asarray(burnable_amount, dtype=np.float64), 0.0, 1.0)
    load = np.clip(np.asarray(burnable_load, dtype=np.float64), 0.0, MAX_SCORE)
    material_load = np.clip(
        np.asarray(burnable_material_load, dtype=np.float64), 0.0, MAX_SCORE
    )
    incoming_aerosol_load = np.clip(
        np.asarray(burnable_incoming_aerosol_load, dtype=np.float64),
        0.0,
        MAX_SCORE,
    )
    vivid = np.clip(np.asarray(vividness, dtype=np.float64), 0.0, 1.0)
    if not (
        view.shape
        == burn.shape
        == load.shape
        == material_load.shape
        == incoming_aerosol_load.shape
        == vivid.shape
    ):
        raise ValueError("instant observer cloud field arrays must have the same shape")

    if incoming_block_risk is not None:
        burn, load, material_load, incoming_aerosol_load, _ = (
            _apply_nonburnable_blocker_consistency_cap(
                view,
                burn,
                load,
                material_load,
                incoming_aerosol_load,
                incoming_block_risk,
            )
        )

    if burnable_gate is not None:
        gate = np.broadcast_to(np.asarray(burnable_gate, dtype=bool), burn.shape)
        burn = np.where(gate, burn, 0.0)
        load = np.where(gate, load, 0.0)
        material_load = np.where(gate, material_load, 0.0)
        incoming_aerosol_load = np.where(gate, incoming_aerosol_load, 0.0)
    active = burn >= ACTIVE_CLOUD_THRESHOLD

    amount_x, amount_y = zip(*CLOUD_AMOUNT_SCORE_POINTS)
    coverage_base = np.interp(burn, amount_x, amount_y)
    material_quality = np.divide(
        material_load,
        MAX_SCORE * burn,
        out=np.zeros_like(material_load),
        where=active,
    )
    material_quality = np.clip(material_quality, 0.0, 1.0)
    material_factor = np.clip(material_quality / MATERIAL_REFERENCE_QUALITY, 0.0, 1.0)
    material_adjustment = coverage_base * (material_factor - 1.0)
    after_material = np.maximum(0.0, coverage_base + material_adjustment)
    incoming_aod_illumination = np.divide(
        incoming_aerosol_load,
        material_load,
        out=np.ones_like(incoming_aerosol_load),
        where=material_load > 1e-9,
    )
    incoming_aod_illumination = np.where(
        active, np.clip(incoming_aod_illumination, 0.0, 1.0), 1.0
    )
    incoming_aod_adjustment = after_material * (incoming_aod_illumination - 1.0)
    after_incoming_aod = np.maximum(0.0, after_material + incoming_aod_adjustment)
    observer_aod_contrast = np.divide(
        load,
        incoming_aerosol_load,
        out=np.ones_like(load),
        where=incoming_aerosol_load > 1e-9,
    )
    observer_aod_contrast = np.where(
        active, np.clip(observer_aod_contrast, 0.0, 1.0), 1.0
    )
    observer_aod_adjustment = after_incoming_aod * (observer_aod_contrast - 1.0)
    raw_quality = np.maximum(0.0, after_incoming_aod + observer_aod_adjustment)

    local_aod = np.broadcast_to(
        np.asarray(local_aod550, dtype=np.float64), burn.shape
    )
    local_aod = np.where(
        np.isfinite(local_aod), np.maximum(local_aod, 0.0), 0.0
    )
    pre_response_index = np.where(
        active,
        np.clip(raw_quality, 0.0, MAX_SCORE),
        0.0,
    )
    index = np.asarray(calibrated_quality_response(pre_response_index), dtype=np.float64)

    return {
        "index": index,
        "pre_response_index": pre_response_index,
        "quality_response_scale": np.full_like(index, QUALITY_RESPONSE_SCALE),
        "quality_response_exponent": np.full_like(index, QUALITY_RESPONSE_EXPONENT),
        "quality_response_adjustment": index - pre_response_index,
        "physically_burnable": active,
        "burnable_cloud_coverage": burn,
        "brightness_color": np.where(active, vivid, 0.0),
        "cloud_material": material_quality,
        "material_factor": material_factor,
        "material_adjustment": np.where(active, material_adjustment, 0.0),
        "incoming_aod_illumination_factor": incoming_aod_illumination,
        "incoming_aod_adjustment": np.where(
            active, incoming_aod_adjustment, 0.0
        ),
        "observer_aod_contrast_factor": observer_aod_contrast,
        "observer_aod_adjustment": np.where(
            active, observer_aod_adjustment, 0.0
        ),
        "coverage_base_score": coverage_base,
        "raw_quality_index": raw_quality,
        "quality_aod550": local_aod,
        "local_aod550": local_aod,
    }


def score_observer_cloud_field_batch(
    view_amount: np.ndarray,
    burnable_amount: np.ndarray,
    burnable_load: np.ndarray,
    burnable_material_load: np.ndarray,
    burnable_incoming_aerosol_load: np.ndarray,
    incoming_block_risk: np.ndarray,
    visibility: np.ndarray,
    vividness: np.ndarray,
    offsets_minutes: np.ndarray,
    local_aod550: np.ndarray | float,
    valid_mask: np.ndarray,
    *,
    analysis_radius_km: float = DEFAULT_ANALYSIS_RADIUS_KM,
    apply_duration: bool = True,
    local_view_amount: np.ndarray | None = None,
    local_burnable_amount: np.ndarray | None = None,
    local_burnable_load: np.ndarray | None = None,
    local_burnable_material_load: np.ndarray | None = None,
    local_burnable_incoming_aerosol_load: np.ndarray | None = None,
    instant_components: Mapping[str, np.ndarray] | None = None,
    local_instant_components: Mapping[str, np.ndarray] | None = None,
) -> list[dict[str, object]]:
    """Score many complete event windows with the scalar formula in one pass.

    Regional requests have the same event offsets for every point but often
    tens of thousands of points. The scalar scorer is intentionally kept as
    the canonical, easy-to-read implementation for point requests; this batch
    path only vectorizes its reductions over the time axis. It applies the
    same native observer-visibility floor as the scalar path, while point-only
    observation gates and score ceilings remain outside this regional path.
    """
    arrays = [
        np.clip(np.asarray(view_amount, dtype=np.float64), 0.0, 1.0),
        np.clip(np.asarray(burnable_amount, dtype=np.float64), 0.0, 1.0),
        np.clip(np.asarray(burnable_load, dtype=np.float64), 0.0, MAX_SCORE),
        np.clip(np.asarray(burnable_material_load, dtype=np.float64), 0.0, MAX_SCORE),
        np.clip(
            np.asarray(burnable_incoming_aerosol_load, dtype=np.float64),
            0.0,
            MAX_SCORE,
        ),
        np.clip(np.asarray(incoming_block_risk, dtype=np.float64), 0.0, 1.0),
        np.clip(np.asarray(visibility, dtype=np.float64), 0.0, 1.0),
        np.clip(np.asarray(vividness, dtype=np.float64), 0.0, 1.0),
    ]
    shape = arrays[0].shape
    if len(shape) != 2 or any(array.shape != shape for array in arrays):
        raise ValueError("observer cloud field batch arrays must share a point x step shape")
    valid = np.asarray(valid_mask, dtype=bool)
    if valid.shape != shape:
        raise ValueError("valid_mask must match observer cloud field arrays")
    offsets = np.asarray(offsets_minutes, dtype=np.float64)
    if offsets.shape != (shape[1],):
        raise ValueError("offsets_minutes must contain one value per event step")

    visibility_gate = arrays[6] >= MIN_OBSERVER_VISIBILITY_FOR_SCORE
    arrays[1] = np.where(visibility_gate, arrays[1], 0.0)
    arrays[2] = np.where(visibility_gate, arrays[2], 0.0)
    arrays[3] = np.where(visibility_gate, arrays[3], 0.0)
    arrays[4] = np.where(visibility_gate, arrays[4], 0.0)
    arrays[1], arrays[2], arrays[3], arrays[4], blocker_scale = (
        _apply_nonburnable_blocker_consistency_cap(
            arrays[0],
            arrays[1],
            arrays[2],
            arrays[3],
            arrays[4],
            arrays[5],
        )
    )

    def broadcast_series(value: np.ndarray | float, name: str) -> np.ndarray:
        result = np.asarray(value, dtype=np.float64)
        try:
            return np.broadcast_to(result, shape)
        except ValueError as exc:
            raise ValueError(f"{name} must broadcast to the point x step shape") from exc

    local_aod = broadcast_series(local_aod550, "local_aod550")
    local_arrays = None
    if local_view_amount is not None and local_burnable_amount is not None:
        local_arrays = [
            np.clip(broadcast_series(local_view_amount, "local_view_amount"), 0.0, 1.0),
            np.clip(broadcast_series(local_burnable_amount, "local_burnable_amount"), 0.0, 1.0),
            np.clip(
                broadcast_series(
                    local_burnable_load
                    if local_burnable_load is not None
                    else local_burnable_amount,
                    "local_burnable_load",
                ),
                0.0,
                MAX_SCORE,
            ),
            np.clip(
                broadcast_series(
                    local_burnable_material_load
                    if local_burnable_material_load is not None
                    else np.zeros(shape),
                    "local_burnable_material_load",
                ),
                0.0,
                MAX_SCORE,
            ),
            np.clip(
                broadcast_series(
                    local_burnable_incoming_aerosol_load
                    if local_burnable_incoming_aerosol_load is not None
                    else np.zeros(shape),
                    "local_burnable_incoming_aerosol_load",
                ),
                0.0,
                MAX_SCORE,
            ),
        ]
    else:
        local_arrays = [np.zeros(shape, dtype=np.float64) for _ in range(5)]

    if (
        instant_components is None
        or not np.all(visibility_gate)
        or np.any(blocker_scale < 1.0 - 1e-12)
    ):
        instant_components = score_instant_observer_cloud_field_batch(
            arrays[0],
            arrays[1],
            arrays[2],
            arrays[3],
            arrays[4],
            arrays[7],
            local_aod,
        )
    if (
        (local_instant_components is None or not np.all(visibility_gate))
        and local_view_amount is not None
        and local_burnable_amount is not None
    ):
        local_arrays[1] = np.where(visibility_gate, local_arrays[1], 0.0)
        local_arrays[2] = np.where(visibility_gate, local_arrays[2], 0.0)
        local_arrays[3] = np.where(visibility_gate, local_arrays[3], 0.0)
        local_arrays[4] = np.where(visibility_gate, local_arrays[4], 0.0)
        local_instant_components = score_instant_observer_cloud_field_batch(
            local_arrays[0],
            local_arrays[1],
            local_arrays[2],
            local_arrays[3],
            local_arrays[4],
            arrays[7],
            local_aod,
        )

    point_count = shape[0]
    results: list[dict[str, object] | None] = [None] * point_count
    # Valid masks are normally a handful of common patterns (the event window
    # ends at different model slices near the domain edge). Grouping them lets
    # every reduction below run over many points without changing the scalar
    # handling of missing slices.
    patterns, inverse = np.unique(valid, axis=0, return_inverse=True)
    duration_x, duration_y = zip(*(
        (0.0, 0.00),
        (5.0, 0.25),
        (10.0, 0.50),
        (15.0, 0.75),
        (20.0, 1.00),
        (40.0, 1.04),
        (70.0, 1.07),
        (120.0, 1.10),
    ))

    def full_window(values: np.ndarray, selected_offsets: np.ndarray) -> np.ndarray:
        # Same per-sample support convention as _time_mean and the window
        # integral (see _time_mean for the trapezoid equivalence).
        widths = _sample_widths(selected_offsets)
        total = np.sum(widths)
        if total <= 0.0:
            return np.mean(values, axis=1)
        return np.sum(values * widths[None, :], axis=1) / total

    def active_window(values: np.ndarray, active: np.ndarray, widths: np.ndarray) -> np.ndarray:
        weighted = active * widths[None, :]
        denominator = np.sum(weighted, axis=1)
        result = np.divide(
            np.sum(values * weighted, axis=1),
            denominator,
            out=np.zeros(values.shape[0], dtype=np.float64),
            where=denominator > 0.0,
        )
        zero_width = denominator <= 0.0
        if np.any(zero_width):
            count = np.count_nonzero(active[zero_width], axis=1)
            fallback = np.divide(
                np.sum(values[zero_width] * active[zero_width], axis=1),
                count,
                out=np.zeros(np.count_nonzero(zero_width), dtype=np.float64),
                where=count > 0,
            )
            result[zero_width] = fallback
        return result

    def clipped_mean(values: np.ndarray, active: np.ndarray, widths: np.ndarray, low: float = 0.0, high: float = 1.0) -> np.ndarray:
        return np.clip(active_window(values, active, widths), low, high)

    def finite_event_mean(values: np.ndarray, fallback: np.ndarray) -> np.ndarray:
        finite = np.isfinite(values)
        count = np.count_nonzero(finite, axis=1)
        total = np.sum(np.where(finite, values, 0.0), axis=1)
        return np.where(count > 0, total / np.maximum(count, 1), fallback)

    for pattern_index, pattern in enumerate(patterns):
        row_ids = np.flatnonzero(inverse == pattern_index)
        selected_indices = np.flatnonzero(pattern)
        if selected_indices.size == 0:
            for row_id in row_ids:
                results[int(row_id)] = {"index": 0.0, "components": {"scoring_scope": SCORING_SCOPE}}
            continue
        selected_offsets = offsets[selected_indices]
        widths = _sample_widths(selected_offsets)
        row_values = [array[row_ids][:, selected_indices] for array in arrays]
        view, burn, load, material_load, incoming_load, incoming_block, visible, vivid = row_values
        active = burn >= ACTIVE_CLOUD_THRESHOLD
        duration = np.sum(active * widths[None, :], axis=1)
        mean_view = clipped_mean(view, active, widths)
        mean_burn = clipped_mean(burn, active, widths)
        mean_load = clipped_mean(load, active, widths, 0.0, MAX_SCORE)
        mean_material_load = clipped_mean(material_load, active, widths, 0.0, MAX_SCORE)
        mean_incoming_load = clipped_mean(incoming_load, active, widths, 0.0, MAX_SCORE)
        full_view = np.clip(full_window(view, selected_offsets), 0.0, 1.0)
        full_burn = np.clip(full_window(burn, selected_offsets), 0.0, 1.0)
        full_load = np.clip(full_window(load, selected_offsets), 0.0, MAX_SCORE)
        full_material_load = np.clip(full_window(material_load, selected_offsets), 0.0, MAX_SCORE)
        full_incoming_load = np.clip(full_window(incoming_load, selected_offsets), 0.0, MAX_SCORE)
        ratio = np.clip(np.divide(mean_burn, np.maximum(mean_view, 1e-6)), 0.0, 1.0)
        ratio = np.where(mean_view > 0.0, ratio, 0.0)

        instant = {
            name: np.asarray(component, dtype=np.float64)[row_ids][:, selected_indices]
            for name, component in instant_components.items()
        }
        # One window formula for every duration: the event composition averages
        # with the weights of its best continuous stretch, normalized by
        # min(total active duration, 20 minutes). Short single-run events
        # recover exactly the previous active-period mean; fragmented and long
        # events no longer jump at the 20-minute branch boundary.
        if apply_duration and bool(np.any(active)):
            score_weights, duration_window_minutes = (
                _unified_event_window_weights_batch(
                    instant["pre_response_index"], active, widths, selected_offsets
                )
            )
        else:
            score_weights = _normalized_active_weights(active, widths)
            duration_window_minutes = duration.copy()
        coverage_base = np.clip(
            np.sum(instant["coverage_base_score"] * score_weights, axis=1),
            0.0,
            MAX_SCORE,
        )
        material_quality = np.clip(
            np.sum(instant["cloud_material"] * score_weights, axis=1), 0.0, 1.0
        )
        material_factor = np.clip(
            np.sum(instant["material_factor"] * score_weights, axis=1), 0.0, 1.0
        )
        material_delta = np.sum(
            instant["material_adjustment"] * score_weights, axis=1
        )
        after_material = np.maximum(0.0, coverage_base + material_delta)
        incoming_factor = np.clip(
            np.sum(instant["incoming_aod_illumination_factor"] * score_weights, axis=1),
            0.0,
            1.0,
        )
        incoming_factor = np.where(np.any(active, axis=1), incoming_factor, 1.0)
        incoming_delta = np.sum(
            instant["incoming_aod_adjustment"] * score_weights, axis=1
        )
        after_incoming = np.maximum(0.0, after_material + incoming_delta)
        observer_factor = np.clip(
            np.sum(instant["observer_aod_contrast_factor"] * score_weights, axis=1),
            0.0,
            1.0,
        )
        observer_factor = np.where(np.any(active, axis=1), observer_factor, 1.0)
        observer_delta = np.sum(
            instant["observer_aod_adjustment"] * score_weights, axis=1
        )
        after_aerosol = np.maximum(0.0, after_incoming + observer_delta)
        visible_cloud = view >= ACTIVE_CLOUD_THRESHOLD
        visible_count = np.count_nonzero(visible_cloud, axis=1)
        surface_gate = np.max(np.where(active, visible, 0.0), axis=1)
        surface_gate = np.where(np.any(active, axis=1), surface_gate, 0.0)
        incoming_block_mean = np.divide(
            np.sum(np.where(visible_cloud, incoming_block, 0.0), axis=1),
            visible_count,
            out=np.zeros(row_ids.size, dtype=np.float64),
            where=visible_count > 0,
        )
        incoming_block_peak = np.max(
            np.where(visible_cloud, incoming_block, 0.0), axis=1
        )
        incoming_block_peak = np.where(visible_count > 0, incoming_block_peak, 0.0)
        duration_multiplier = (
            np.interp(duration, duration_x, duration_y)
            if apply_duration
            else np.ones(row_ids.size, dtype=np.float64)
        )
        duration_delta = after_aerosol * (duration_multiplier - 1.0)
        after_duration = np.maximum(0.0, after_aerosol + duration_delta)
        duration_window_pre_response = after_aerosol.copy()
        pre_response_score = np.where(
            np.any(active, axis=1),
            np.clip(after_duration, 0.0, MAX_SCORE),
            0.0,
        )
        final_score = np.asarray(
            calibrated_quality_response(pre_response_score), dtype=np.float64
        )
        local_view, local_burn, local_load, local_material, local_incoming = [
            array[row_ids][:, selected_indices] for array in local_arrays
        ]
        local_full_burn = np.clip(full_window(local_burn, selected_offsets), 0.0, 1.0)
        local_active = local_burn >= ACTIVE_CLOUD_THRESHOLD
        local_scores = None
        local_peaks = None
        if local_view_amount is not None and local_burnable_amount is not None and local_instant_components is not None:
            local_instant = {
                name: np.asarray(component, dtype=np.float64)[row_ids][:, selected_indices]
                for name, component in local_instant_components.items()
            }
            if apply_duration and bool(np.any(local_active)):
                local_score_weights, _ = _unified_event_window_weights_batch(
                    local_instant["pre_response_index"],
                    local_active,
                    widths,
                    selected_offsets,
                )
            else:
                local_score_weights = _normalized_active_weights(local_active, widths)
            local_coverage = np.clip(
                np.sum(local_instant["coverage_base_score"] * local_score_weights, axis=1),
                0.0,
                MAX_SCORE,
            )
            local_material_delta = np.sum(
                local_instant["material_adjustment"] * local_score_weights, axis=1
            )
            local_after_material = np.maximum(0.0, local_coverage + local_material_delta)
            local_incoming_delta = np.sum(
                local_instant["incoming_aod_adjustment"] * local_score_weights, axis=1
            )
            local_after_incoming = np.maximum(0.0, local_after_material + local_incoming_delta)
            local_observer_delta = np.sum(
                local_instant["observer_aod_adjustment"] * local_score_weights, axis=1
            )
            local_after_aerosol = np.maximum(0.0, local_after_incoming + local_observer_delta)
            local_duration = np.sum(local_active * widths[None, :], axis=1)
            local_multiplier = (
                np.interp(local_duration, duration_x, duration_y)
                if apply_duration
                else np.ones(row_ids.size, dtype=np.float64)
            )
            local_after_duration = np.maximum(
                0.0, local_after_aerosol * local_multiplier
            )
            local_pre_response_scores = np.where(
                np.any(local_active, axis=1),
                np.clip(local_after_duration, 0.0, MAX_SCORE),
                0.0,
            )
            local_scores = np.asarray(
                calibrated_quality_response(local_pre_response_scores), dtype=np.float64
            )
            # The scalar diagnostic reports the maximum instantaneous local
            # score, including any weak but physically valid tail below the
            # event's dynamic duration threshold.
            local_peaks = np.max(local_instant["index"], axis=1)
            local_scores = np.where(np.any(local_active, axis=1), local_scores, 0.0)
        else:
            local_scores = np.full(row_ids.size, np.nan, dtype=np.float64)
            local_peaks = np.full(row_ids.size, np.nan, dtype=np.float64)

        local_view_peaks = np.max(local_view, axis=1)
        local_burn_peaks = np.max(local_burn, axis=1)

        local_aod_rows = local_aod[row_ids][:, selected_indices]
        local_aod_values = np.maximum(0.0, finite_event_mean(local_aod_rows, np.zeros(row_ids.size)))
        # Scalar, batch, and national wrapper diagnostics all select their
        # representative instant by post-leakage, post-gate burnable area.
        peak_indices = np.argmax(burn, axis=1)
        row_index = np.arange(row_ids.size)
        peak_view = view[row_index, peak_indices]
        peak_burn = burn[row_index, peak_indices]
        peak_load = load[row_index, peak_indices]
        peak_material = material_load[row_index, peak_indices]
        peak_ratio = np.where(
            peak_view > 0.0,
            np.clip(peak_burn / np.maximum(peak_view, 1e-6), 0.0, 1.0),
            0.0,
        )
        local_full_view = np.clip(full_window(local_view, selected_offsets), 0.0, 1.0)
        local_full_burn = np.clip(full_window(local_burn, selected_offsets), 0.0, 1.0)
        local_full_load = np.clip(full_window(local_load, selected_offsets), 0.0, MAX_SCORE)
        local_full_material = np.clip(full_window(local_material, selected_offsets), 0.0, MAX_SCORE)
        local_active_any = np.any(local_active, axis=1)
        visible_active_count = np.count_nonzero(visible_cloud & active, axis=1)
        vivid_count = np.count_nonzero(active, axis=1)
        brightness = np.divide(
            np.sum(np.where(active, vivid, 0.0), axis=1),
            vivid_count,
            out=np.zeros(row_ids.size, dtype=np.float64),
            where=vivid_count > 0,
        )
        physical_steps = np.count_nonzero(active, axis=1).astype(np.float64)
        event_threshold = ACTIVE_CLOUD_THRESHOLD
        limiting: list[str] = []
        for index in range(row_ids.size):
            limiting.append(
                "不可烧云挡光" if np.any(blocker_scale[row_ids[index], selected_indices] < 1.0 - 1e-12)
                else "无可烧云" if mean_burn[index] < ACTIVE_CLOUD_THRESHOLD
                else "太阳入射阻挡已进入可烧云面积" if incoming_block_mean[index] >= 0.55
                else "云质与垂直厚度" if material_factor[index] < 0.70
                else "太阳入射 AOD 削光" if incoming_factor[index] < 0.70
                else "观测光路 AOD 降低对比度" if observer_factor[index] < 0.70
                else "可烧云绝对量"
            )
        source = (
            f"观测点朝太阳方向 ±90°、{float(analysis_radius_km):.0f} km 半圆内"
            "全部 GFS 云格投影到观测者前方天空，按方位与仰角的立体角计算可见及可烧云占比；"
            "观测点所在水平云格覆盖到天顶，远处云格按近边/远边实际仰角贡献；"
            "每个云底分别追踪太阳入射与云底到观测者的回程光路，逐格积分 CAMS AOD 与"
            "垂直廓线得到的消光系数；入射段决定云底受光，回程段独立修正对比度与饱和度；"
            "再按观测视线由近到远遮挡合成；"
            "云量质量仅统计物理可烧时段，全窗口零值只进入持续时间；"
            "云量只由可见且受光的云底面积决定；云质、入射 AOD、观测 AOD 与持续时间各修正一次；"
            "入射阻挡仅在超过原生可烧/可见互补关系时作为不可烧云上限，不重复扣分；"
            "不选 Top-K、不选代表云、不使用镜头焦段"
        )
        for local_index, row_id in enumerate(row_ids):
            components = {
                "scoring_scope": SCORING_SCOPE,
                "score_formula": OBSERVER_CLOUD_FIELD_SCORE_FORMULA,
                "score_max": MAX_SCORE,
                "forward_sector_half_angle_deg": 90.0,
                "forward_field_radius_km": float(analysis_radius_km),
                "forward_view_cloud_field_amount_peak": float(np.clip(peak_view[local_index], 0.0, 1.0)),
                "forward_view_cloud_field_amount_mean": float(mean_view[local_index]),
                "forward_view_cloud_field_amount_effective": float(mean_view[local_index]),
                "forward_view_cloud_field_amount_full_window_mean": float(full_view[local_index]),
                "forward_burnable_cloud_field_amount_peak": float(np.clip(peak_burn[local_index], 0.0, 1.0)),
                "forward_burnable_cloud_field_amount_mean": float(mean_burn[local_index]),
                "forward_burnable_cloud_field_amount_effective": float(mean_burn[local_index]),
                "forward_burnable_cloud_field_amount_full_window_mean": float(full_burn[local_index]),
                "forward_burnable_cloud_ratio_peak": float(peak_ratio[local_index]),
                "forward_burnable_cloud_ratio_effective": float(ratio[local_index]),
                "forward_burnable_cloud_load_peak": float(np.clip(peak_load[local_index], 0.0, MAX_SCORE)),
                "forward_burnable_cloud_load_mean": float(mean_load[local_index]),
                "forward_burnable_cloud_load_effective": float(mean_load[local_index]),
                "forward_burnable_cloud_load_full_window_mean": float(full_load[local_index]),
                "forward_burnable_cloud_material_load_peak": float(np.clip(peak_material[local_index], 0.0, MAX_SCORE)),
                "forward_burnable_cloud_material_load_mean": float(mean_material_load[local_index]),
                "forward_burnable_cloud_material_load_effective": float(mean_material_load[local_index]),
                "forward_burnable_cloud_material_load_full_window_mean": float(full_material_load[local_index]),
                "forward_burnable_cloud_incoming_aerosol_load_mean": float(mean_incoming_load[local_index]),
                "forward_burnable_cloud_incoming_aerosol_load_effective": float(mean_incoming_load[local_index]),
                "forward_burnable_cloud_incoming_aerosol_load_full_window_mean": float(full_incoming_load[local_index]),
                "burnable_cloud_coverage_peak": float(np.clip(peak_burn[local_index], 0.0, 1.0)),
                "burnable_cloud_coverage_mean": float(mean_burn[local_index]),
                "burnable_cloud_coverage_effective": float(mean_burn[local_index]),
                "burnable_to_view_ratio": float(ratio[local_index]),
                "coverage_base_score": float(coverage_base[local_index]),
                "coverage_score": float(coverage_base[local_index]),
                "cloud_material": float(material_quality[local_index]),
                "material_reference_quality": MATERIAL_REFERENCE_QUALITY,
                "material_factor": float(material_factor[local_index]),
                "material_adjustment": float(material_delta[local_index]),
                "incoming_aod_illumination_factor": float(incoming_factor[local_index]),
                "incoming_aod_adjustment": float(incoming_delta[local_index]),
                "observer_aod_contrast_factor": float(observer_factor[local_index]),
                "observer_aod_adjustment": float(observer_delta[local_index]),
                "burnable_ratio_factor": 1.0,
                "base_quality_index": float(coverage_base[local_index]),
                "raw_quality_index": float(after_aerosol[local_index]),
                "pre_response_quality_index": float(pre_response_score[local_index]),
                "quality_response_scale": QUALITY_RESPONSE_SCALE,
                "quality_response_exponent": QUALITY_RESPONSE_EXPONENT,
                "quality_response_adjustment": float(
                    final_score[local_index] - pre_response_score[local_index]
                ),
                "duration_minutes": float(duration[local_index]),
                "duration_factor": float(duration_multiplier[local_index]),
                "duration_window_minutes": float(duration_window_minutes[local_index]),
                "duration_window_pre_response_index": float(
                    duration_window_pre_response[local_index]
                ),
                "duration_adjustment": float(duration_delta[local_index]),
                "quality_aod550": float(local_aod_values[local_index]),
                "local_aod550": float(local_aod_values[local_index]),
                "solar_incoming_block_risk_peak": float(incoming_block_peak[local_index]),
                "solar_incoming_block_risk_mean": float(incoming_block_mean[local_index]),
                "solar_incoming_block_risk_effective": float(incoming_block_mean[local_index]),
                "observer_local_cloud_field_amount_peak": float(local_view_peaks[local_index]),
                "observer_local_cloud_field_amount_mean": float(local_full_view[local_index]),
                "observer_local_burnable_cloud_field_amount_peak": float(local_burn_peaks[local_index]),
                "observer_local_burnable_cloud_field_amount_mean": float(local_full_burn[local_index]),
                "observer_local_burnable_cloud_load_peak": float(np.max(local_load[local_index])),
                "observer_local_burnable_cloud_load_mean": float(local_full_load[local_index]),
                "observer_local_burnable_cloud_material_load_peak": float(np.max(local_material[local_index])),
                "observer_local_burnable_cloud_material_load_mean": float(local_full_material[local_index]),
                "observer_column_quality_index": None if np.isnan(local_scores[local_index]) else float(local_scores[local_index]),
                "observer_column_quality_peak": None if np.isnan(local_peaks[local_index]) else float(local_peaks[local_index]),
                "observer_column_quality_source": (
                    "同一评分公式应用于观测点本地 GFS 云柱，仅作单点/范围诊断；"
                    "它不替换完整前方天空的区域质量指数"
                    if local_view_amount is not None and local_burnable_amount is not None
                    else "未提供观测点本地云柱诊断"
                ),
                "surface_visibility_gate_peak": float(surface_gate[local_index]),
                "brightness_color": float(brightness[local_index]),
                "quality_limiting_factor": limiting[local_index],
                "physically_burnable_steps": float(physical_steps[local_index]),
                "event_active_cloud_threshold": float(event_threshold),
                "observation_burnability_veto_steps": 0.0,
                "quality_coverage_source": source,
            }
            results[int(row_id)] = {
                "index": float(final_score[local_index]),
                "components": components,
            }
    return [
        result
        if result is not None
        else {"index": 0.0, "components": {"scoring_scope": SCORING_SCOPE}}
        for result in results
    ]


def _use_rust_empirical() -> bool:
    backend = os.environ.get("FIRESKY_EMPIRICAL_BACKEND", "python").strip().lower()
    if backend not in {"python", "rust"}:
        raise ValueError("FIRESKY_EMPIRICAL_BACKEND must be python or rust")
    return backend == "rust"


def duration_factor(duration_minutes: float) -> float:
    """Normalize the event integral to 20 minutes, then saturate gradually."""
    if _use_rust_empirical():
        from firesky.empirical_native import duration_factor as native_duration_factor
        return native_duration_factor(duration_minutes)
    return _piecewise(
        max(0.0, float(duration_minutes)),
        (
            (0.0, 0.00),
            (5.0, 0.25),
            (10.0, 0.50),
            (15.0, 0.75),
            (20.0, 1.00),
            (40.0, 1.04),
            (70.0, 1.07),
            (120.0, 1.10),
        ),
    )


CONTINUOUS_DURATION_WINDOW_MINUTES = 20.0


def _window_candidate_weight_rows(
    pattern: np.ndarray,
    widths: np.ndarray,
    window_minutes: float,
) -> tuple[np.ndarray, float]:
    """Return candidate window weight rows plus the normalization denominator.

    Each row places a candidate ``window_minutes`` window on the event axis
    and weights every active sample by its overlap with that window, divided
    by ``min(total active duration, window_minutes)``. A single run shorter
    than the window therefore keeps its plain active-period mean, while the
    score of fragmented events is governed by their best continuous stretch
    instead of averaging runs that a 20-minute observer could never see
    together. Windows may bridge inactive gaps: the gap itself contributes no
    integral, it only limits which active samples fit inside the window.
    """
    sample_widths = np.maximum(np.asarray(widths, dtype=np.float64), 0.0)
    edges = np.concatenate(([0.0], np.cumsum(sample_widths)))
    total_active = float(np.sum(np.where(pattern, sample_widths, 0.0)))
    if total_active <= 0.0:
        return np.empty((0, pattern.size), dtype=np.float64), 0.0
    denominator = min(total_active, float(window_minutes))
    max_start = max(0.0, float(edges[-1]) - window_minutes)
    candidates = {0.0, max_start}
    for edge in edges:
        candidates.add(float(np.clip(edge, 0.0, max_start)))
        candidates.add(float(np.clip(edge - window_minutes, 0.0, max_start)))
    rows = []
    for start in sorted(candidates):
        stop = start + window_minutes
        overlaps = np.minimum(edges[1:], stop) - np.maximum(edges[:-1], start)
        overlaps = np.maximum(overlaps, 0.0) * pattern
        rows.append(overlaps / denominator)
    return np.asarray(rows, dtype=np.float64), denominator


def _unified_event_window_weights(
    values: np.ndarray,
    active: np.ndarray,
    widths: np.ndarray,
    offsets: np.ndarray | None = None,
) -> tuple[np.ndarray, float]:
    """Return best-window weights for one event and the window's active width.

    The objective integrates the per-step pre-response index over active
    samples inside each candidate window; the selected weights are what the
    event composition should average with so the score follows the strongest
    continuous stretch of the event.
    """
    if _use_rust_empirical():
        from firesky.empirical_native import event_window_weights
        return event_window_weights(values, active, widths, offsets)
    values = np.asarray(values, dtype=np.float64)
    active = np.asarray(active, dtype=bool)
    widths = np.asarray(widths, dtype=np.float64)
    order = None
    if offsets is not None:
        order = np.argsort(np.asarray(offsets, dtype=np.float64), kind="stable")
        values = values[order]
        active = active[order]
        widths = widths[order]
    rows, denominator = _window_candidate_weight_rows(
        active, widths, CONTINUOUS_DURATION_WINDOW_MINUTES
    )
    if rows.size == 0:
        return np.zeros(active.size, dtype=np.float64), 0.0
    objective = rows @ values
    best = int(np.argmax(objective))
    weights = np.empty(active.size, dtype=np.float64)
    # The caller composes against arrays in the original column order, so the
    # sorted-space weights are scattered back before returning.
    if order is not None:
        weights[order] = rows[best]
    else:
        weights = rows[best].copy()
    return weights, float(np.sum(rows[best]) * denominator)


def _unified_event_window_weights_batch(
    values: np.ndarray,
    active: np.ndarray,
    widths: np.ndarray,
    offsets: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Row-wise best-window weights, grouping rows by shared activity mask."""
    if _use_rust_empirical():
        from firesky.empirical_native import event_window_weights_batch
        return event_window_weights_batch(values, active, widths, offsets)
    values = np.asarray(values, dtype=np.float64)
    active = np.asarray(active, dtype=bool)
    if values.ndim != 2 or active.shape != values.shape:
        raise ValueError("values and active must be aligned two-dimensional arrays")
    widths = np.asarray(widths, dtype=np.float64)
    order = None
    if offsets is not None:
        order = np.argsort(np.asarray(offsets, dtype=np.float64), kind="stable")
        values = values[:, order]
        active = active[:, order]
        widths = widths[order]
    weights = np.zeros(values.shape, dtype=np.float64)
    window_minutes = np.zeros(values.shape[0], dtype=np.float64)
    if not np.any(active):
        return weights, window_minutes
    patterns, inverse = np.unique(active, axis=0, return_inverse=True)
    for pattern_index, pattern in enumerate(patterns):
        row_ids = np.flatnonzero(inverse == pattern_index)
        rows, denominator = _window_candidate_weight_rows(
            pattern, widths, CONTINUOUS_DURATION_WINDOW_MINUTES
        )
        if rows.size == 0:
            continue
        objective = values[row_ids] @ rows.T
        best = np.argmax(objective, axis=1)
        selected = rows[best]
        if order is not None:
            # Scatter the sorted-space weights back to the caller's column
            # order so downstream compositions stay aligned.
            original = np.zeros_like(selected)
            original[:, order] = selected
            selected = original
        weights[row_ids] = selected
        window_minutes[row_ids] = np.sum(selected, axis=1) * denominator
    return weights, window_minutes


def _normalized_active_weights(
    active: np.ndarray,
    widths: np.ndarray,
) -> np.ndarray:
    """Return width-normalized active weights (the plain active-period mean)."""
    weighted = np.asarray(active, dtype=np.float64) * np.maximum(
        np.asarray(widths, dtype=np.float64), 0.0
    )
    if weighted.ndim == 1:
        total = float(np.sum(weighted))
        return weighted / total if total > 0.0 else np.zeros_like(weighted)
    total = np.sum(weighted, axis=1, keepdims=True)
    return np.divide(
        weighted,
        total,
        out=np.zeros_like(weighted),
        where=total > 0.0,
    )


def duration_adjustment(duration_minutes: float, base_score: float = 1.0) -> float:
    """Return the signed duration correction in score points."""
    return float(base_score) * (duration_factor(duration_minutes) - 1.0)


def score_observer_cloud_field(
    view_amount: Sequence[float],
    burnable_amount: Sequence[float],
    burnable_load: Sequence[float],
    burnable_material_load: Sequence[float],
    burnable_incoming_aerosol_load: Sequence[float],
    incoming_block_risk: Sequence[float],
    visibility: Sequence[float],
    vividness: Sequence[float],
    offsets_minutes: Sequence[float],
    local_aod550: Sequence[float] | float,
    valid_indices: Sequence[int] | None = None,
    burnable_gate: Sequence[bool] | None = None,
    score_ceiling: Sequence[float] | float | None = None,
    *,
    analysis_radius_km: float = DEFAULT_ANALYSIS_RADIUS_KM,
    apply_duration: bool = True,
    local_view_amount: Sequence[float] | None = None,
    local_burnable_amount: Sequence[float] | None = None,
    local_burnable_load: Sequence[float] | None = None,
    local_burnable_material_load: Sequence[float] | None = None,
    local_burnable_incoming_aerosol_load: Sequence[float] | None = None,
    _instant_components: Mapping[str, np.ndarray] | None = None,
    _local_instant_components: Mapping[str, np.ndarray] | None = None,
) -> dict[str, object]:
    """Score the whole forward cloud field with one auditable formula.

    ``burnable_amount`` is the fraction of the complete forward azimuth field
    occupied by GFS cloud columns whose bases receive sunlight. Each azimuth
    bin combines the strongest valid horizontal cloud amount with the radial
    support mean, so repeated radial samples do not turn an 80% model cloud
    field into 100% while an isolated burnable patch cannot represent the whole
    line of sight.
    A cloud fraction is horizontal coverage, so it is not multiplied by the
    apparent vertical height of a high cloud and is not a camera focal-length
    estimate.
    Cloud base/top are used for the incoming solar ray. Observer visibility is
    already resolved by near-to-far sky compositing in the native kernel; a
    field below the physical visibility floor is therefore vetoed before the
    cloud-area score is calculated. It is the only input that establishes the
    observer-side cloud-amount gate.
    ``incoming_block_risk`` is a solar-incoming diagnostic supplied by the
    native geometry pass. It only caps unexplained burnable area beyond the
    native burnable/view complement, so already-accounted geometry is not
    penalized twice.
    The remaining inputs are diagnostics or small corrections.
    """
    arrays = [
        np.clip(np.asarray(view_amount, dtype=np.float64), 0.0, 1.0),
        np.clip(np.asarray(burnable_amount, dtype=np.float64), 0.0, 1.0),
        np.clip(np.asarray(burnable_load, dtype=np.float64), 0.0, 2.5),
        np.clip(np.asarray(burnable_material_load, dtype=np.float64), 0.0, 2.5),
        np.clip(
            np.asarray(burnable_incoming_aerosol_load, dtype=np.float64),
            0.0,
            2.5,
        ),
        np.clip(np.asarray(incoming_block_risk, dtype=np.float64), 0.0, 1.0),
        np.clip(np.asarray(visibility, dtype=np.float64), 0.0, 1.0),
        np.clip(np.asarray(vividness, dtype=np.float64), 0.0, 1.0),
        np.asarray(offsets_minutes, dtype=np.float64),
    ]
    if len({array.size for array in arrays}) != 1:
        raise ValueError("observer cloud field arrays must have the same length")

    if valid_indices is None:
        indices = np.arange(arrays[0].size, dtype=np.int64)
    else:
        indices = np.asarray(valid_indices, dtype=np.int64)
    if indices.size == 0:
        return {"index": 0.0, "components": {"scoring_scope": SCORING_SCOPE}}

    view, burn, load, material_load, incoming_aerosol_load, incoming_block, visible, vivid, offsets = [
        array[indices] for array in arrays
    ]
    local_view = (
        np.clip(np.asarray(local_view_amount, dtype=np.float64), 0.0, 1.0)[indices]
        if local_view_amount is not None
        else np.zeros(indices.size, dtype=np.float64)
    )
    local_burn = (
        np.clip(np.asarray(local_burnable_amount, dtype=np.float64), 0.0, 1.0)[indices]
        if local_burnable_amount is not None
        else np.zeros(indices.size, dtype=np.float64)
    )
    local_load = (
        np.clip(
            np.asarray(
                local_burnable_load
                if local_burnable_load is not None
                else local_burnable_amount
                if local_burnable_amount is not None
                else np.zeros(arrays[0].size),
                dtype=np.float64,
            ),
            0.0,
            2.5,
        )[indices]
    )
    local_material_load = (
        np.clip(
            np.asarray(
                local_burnable_material_load
                if local_burnable_material_load is not None
                else np.zeros(arrays[0].size),
                dtype=np.float64,
            ),
            0.0,
            2.5,
        )[indices]
    )
    local_incoming_aerosol_load = (
        np.clip(
            np.asarray(
                local_burnable_incoming_aerosol_load
                if local_burnable_incoming_aerosol_load is not None
                else np.zeros(arrays[0].size),
                dtype=np.float64,
            ),
            0.0,
            2.5,
        )[indices]
    )
    gate = (
        np.ones(indices.size, dtype=bool)
        if burnable_gate is None
        else np.asarray(burnable_gate, dtype=bool)[indices]
    )
    if score_ceiling is None:
        ceilings = np.full(indices.size, MAX_SCORE, dtype=np.float64)
        has_score_ceiling = False
    else:
        raw_ceilings = np.asarray(score_ceiling, dtype=np.float64)
        if raw_ceilings.ndim == 0:
            raw_ceilings = np.full(arrays[0].size, raw_ceilings, dtype=np.float64)
        else:
            raw_ceilings = np.broadcast_to(raw_ceilings, (arrays[0].size,))
        ceilings = np.clip(raw_ceilings[indices], 0.0, MAX_SCORE)
        has_score_ceiling = True
    # The external gate is an observation-validity veto.  The visibility floor
    # is the native observer-side physical gate: receiving sunlight alone does
    # not make a cloud visible from the ground. Neither gate changes the view
    # field itself or adds a second continuous penalty.
    visibility_gate = visible >= MIN_OBSERVER_VISIBILITY_FOR_SCORE
    effective_gate = gate & visibility_gate
    burn = np.where(effective_gate, burn, 0.0)
    load = np.where(effective_gate, load, 0.0)
    material_load = np.where(effective_gate, material_load, 0.0)
    incoming_aerosol_load = np.where(effective_gate, incoming_aerosol_load, 0.0)
    burn, load, material_load, incoming_aerosol_load, blocker_scale = (
        _apply_nonburnable_blocker_consistency_cap(
            view,
            burn,
            load,
            material_load,
            incoming_aerosol_load,
            incoming_block,
        )
    )

    # The observer cell already occupies its physical angular footprint in the
    # native sky projection. The local column remains diagnostic and never
    # replaces the complete forward-sky field.
    local_burn = np.where(effective_gate, local_burn, 0.0)
    local_load = np.where(effective_gate, local_load, 0.0)
    local_material_load = np.where(effective_gate, local_material_load, 0.0)
    local_incoming_aerosol_load = np.where(effective_gate, local_incoming_aerosol_load, 0.0)

    peak_index = int(np.argmax(burn)) if burn.size else 0
    peak_view = float(view[peak_index]) if view.size else 0.0
    peak_burn = float(burn[peak_index]) if burn.size else 0.0
    peak_load = float(load[peak_index]) if load.size else 0.0
    peak_material_load = float(material_load[peak_index]) if material_load.size else 0.0
    event_active_threshold = ACTIVE_CLOUD_THRESHOLD
    cloud_active = _event_active_cloud(burn)
    # ``burn`` is already the final observer-visible, solar-illuminated cloud
    # area. Applying the incoming/occlusion diagnostic again would count the
    # same cloud obstruction twice.
    active = cloud_active
    duration = _active_duration_minutes(offsets, active)

    # Cloud quality describes the cloud field while it can actually receive
    # and return sunset/sunrise light. Zeroes before illumination begins or
    # after it ends belong exclusively to the duration measurement. Including
    # them in the cloud-area average would apply duration twice.
    mean_view = _clip(_active_time_mean(view, offsets, active))
    mean_burn = _clip(_active_time_mean(burn, offsets, active))
    mean_load = _clip(_active_time_mean(load, offsets, active), 0.0, 2.5)
    mean_material_load = _clip(
        _active_time_mean(material_load, offsets, active), 0.0, 2.5
    )
    mean_incoming_aerosol_load = _clip(
        _active_time_mean(incoming_aerosol_load, offsets, active), 0.0, 2.5
    )
    full_window_view = _clip(_time_mean(view, offsets))
    full_window_burn = _clip(_time_mean(burn, offsets))
    full_window_load = _clip(_time_mean(load, offsets), 0.0, 2.5)
    full_window_material_load = _clip(
        _time_mean(material_load, offsets), 0.0, 2.5
    )
    full_window_incoming_aerosol_load = _clip(
        _time_mean(incoming_aerosol_load, offsets), 0.0, 2.5
    )
    ratio = _clip(mean_burn / max(mean_view, 1e-6)) if mean_view > 0.0 else 0.0

    # Complete the physical score independently at every event minute before
    # time aggregation. Averaging cloud, material and AOD inputs separately
    # can pair a strong cloud from one minute with a clear path from another,
    # producing an event score that no instant actually achieved.
    if (
        _instant_components is not None
        and np.all(effective_gate)
        and not np.any(blocker_scale < 1.0 - 1e-12)
    ):
        instant = {
            name: np.asarray(component, dtype=np.float64)[indices]
            for name, component in _instant_components.items()
        }
    else:
        instant = score_instant_observer_cloud_field_batch(
            view,
            burn,
            load,
            material_load,
            incoming_aerosol_load,
            vivid,
            local_aod550,
        )
    # One window formula for every duration: with duration applied, the event
    # composition averages with the weights of its best continuous stretch
    # normalized by min(total active duration, 20 minutes); without duration
    # weighting it keeps the plain active-period mean. Short single-run
    # events recover exactly the previous behavior, and the former
    # duration>20 branch discontinuity is gone.
    if apply_duration and np.any(active):
        score_weights, duration_window_minutes = _unified_event_window_weights(
            np.asarray(instant["pre_response_index"], dtype=np.float64),
            active,
            _sample_widths(offsets),
            offsets,
        )

        def score_time_mean(values: object) -> float:
            return float(
                np.sum(np.asarray(values, dtype=np.float64) * score_weights)
            )

    else:
        duration_window_minutes = duration

        def score_time_mean(values: object) -> float:
            return _active_time_mean(values, offsets, active)

    coverage_base = _clip(
        score_time_mean(instant["coverage_base_score"]),
        0.0,
        MAX_SCORE,
    )
    material_quality = _clip(score_time_mean(instant["cloud_material"]))
    material_factor = _clip(score_time_mean(instant["material_factor"]))
    material_delta = score_time_mean(instant["material_adjustment"])
    after_material = max(0.0, coverage_base + material_delta)
    incoming_aod_illumination = (
        _clip(score_time_mean(instant["incoming_aod_illumination_factor"]))
        if np.any(active)
        else 1.0
    )
    incoming_aod_delta = score_time_mean(instant["incoming_aod_adjustment"])
    after_incoming_aod = max(0.0, after_material + incoming_aod_delta)
    observer_aod_contrast = (
        _clip(score_time_mean(instant["observer_aod_contrast_factor"]))
        if np.any(active)
        else 1.0
    )
    observer_aod_delta = score_time_mean(instant["observer_aod_adjustment"])
    after_aerosol = max(0.0, after_incoming_aod + observer_aod_delta)
    visible_cloud = view >= ACTIVE_CLOUD_THRESHOLD
    incoming_block_mean = float(np.mean(incoming_block[visible_cloud])) if np.any(visible_cloud) else 0.0
    incoming_block_peak = float(np.max(incoming_block[visible_cloud])) if np.any(visible_cloud) else 0.0
    uncapped_after_ceiling = after_aerosol
    instant_indexes = np.asarray(instant["pre_response_index"], dtype=np.float64)
    if has_score_ceiling and np.any(active):
        pre_response_ceilings = np.asarray(
            inverse_calibrated_quality_response(ceilings), dtype=np.float64
        )
        bounded_instant_indexes = np.minimum(instant_indexes, pre_response_ceilings)
        after_ceiling = score_time_mean(bounded_instant_indexes)
        effective_score_ceiling = score_time_mean(ceilings)
    else:
        bounded_instant_indexes = instant_indexes
        after_ceiling = after_aerosol
        effective_score_ceiling = MAX_SCORE
    observation_ceiling_delta = after_ceiling - uncapped_after_ceiling

    raw_duration_multiplier = duration_factor(duration) if apply_duration and np.any(active) else 1.0
    # Duration is applied here once and cannot create cloud area. The window
    # reduction above already selected the best continuous stretch, so no
    # second branch exists at the 20-minute boundary.
    duration_multiplier = raw_duration_multiplier
    duration_delta = after_ceiling * (duration_multiplier - 1.0)
    after_duration = max(0.0, after_ceiling + duration_delta)
    duration_window_pre_response = after_ceiling

    local_aod = _finite_mean(local_aod550)
    effective_pre_response_ceiling = float(
        inverse_calibrated_quality_response(effective_score_ceiling)
    )
    pre_response_score = (
        _clip(min(after_duration, effective_pre_response_ceiling), 0.0, MAX_SCORE)
        if np.any(active)
        else 0.0
    )
    final_score = float(calibrated_quality_response(pre_response_score))

    # Keep an explicitly local diagnostic as well. It is never substituted for
    # the complete forward-sky score: a point column cannot establish the
    # angular area occupied by the whole visible cloud field.
    local_quality_index: float | None = None
    local_quality_peak: float | None = None
    if local_view_amount is not None and local_burnable_amount is not None:
        local_active = _event_active_cloud(local_burn)
        if np.any(local_active):
            local_result = score_observer_cloud_field(
                local_view,
                local_burn,
                local_load,
                local_material_load,
                local_incoming_aerosol_load,
                np.zeros_like(incoming_block),
                visible,
                vivid,
                offsets,
                local_aod,
                burnable_gate=effective_gate,
                analysis_radius_km=analysis_radius_km,
                apply_duration=apply_duration,
                _instant_components=_local_instant_components,
            )
            local_quality_index = float(local_result["index"])
            if _local_instant_components is not None and np.all(effective_gate):
                local_instant = {
                    name: np.asarray(component, dtype=np.float64)[indices]
                    for name, component in _local_instant_components.items()
                }
            else:
                local_instant = score_instant_observer_cloud_field_batch(
                    local_view,
                    local_burn,
                    local_load,
                    local_material_load,
                    local_incoming_aerosol_load,
                    vivid,
                    local_aod,
                    effective_gate,
                )
            local_quality_peak = float(
                np.max(np.asarray(local_instant["index"], dtype=np.float64))
            )
        else:
            local_quality_index = 0.0
            local_quality_peak = 0.0


    quality_limiting_factor = (
        "不可烧云挡光" if np.any(blocker_scale < 1.0 - 1e-12)
        else "无可烧云" if mean_burn < ACTIVE_CLOUD_THRESHOLD
        else "太阳入射阻挡已进入可烧云面积" if incoming_block_mean >= 0.55
        else "云质与垂直厚度" if material_factor < 0.70
        else "太阳入射 AOD 削光" if incoming_aod_illumination < 0.70
        else "观测光路 AOD 降低对比度" if observer_aod_contrast < 0.70
        else "可烧云绝对量"
    )
    components = {
        "scoring_scope": SCORING_SCOPE,
        "score_formula": OBSERVER_CLOUD_FIELD_SCORE_FORMULA,
        "score_max": MAX_SCORE,
        "forward_sector_half_angle_deg": 90.0,
        "forward_field_radius_km": float(analysis_radius_km),
        "forward_view_cloud_field_amount_peak": peak_view,
        "forward_view_cloud_field_amount_mean": mean_view,
        "forward_view_cloud_field_amount_effective": mean_view,
        "forward_view_cloud_field_amount_full_window_mean": full_window_view,
        "forward_burnable_cloud_field_amount_peak": peak_burn,
        "forward_burnable_cloud_field_amount_mean": mean_burn,
        "forward_burnable_cloud_field_amount_effective": mean_burn,
        "forward_burnable_cloud_field_amount_full_window_mean": full_window_burn,
        "forward_burnable_cloud_ratio_peak": _clip(peak_burn / max(peak_view, 1e-6)) if peak_view else 0.0,
        "forward_burnable_cloud_ratio_effective": ratio,
        "forward_burnable_cloud_load_peak": peak_load,
        "forward_burnable_cloud_load_mean": mean_load,
        "forward_burnable_cloud_load_effective": mean_load,
        "forward_burnable_cloud_load_full_window_mean": full_window_load,
        "forward_burnable_cloud_material_load_peak": peak_material_load,
        "forward_burnable_cloud_material_load_mean": mean_material_load,
        "forward_burnable_cloud_material_load_effective": mean_material_load,
        "forward_burnable_cloud_material_load_full_window_mean": full_window_material_load,
        "forward_burnable_cloud_incoming_aerosol_load_mean": mean_incoming_aerosol_load,
        "forward_burnable_cloud_incoming_aerosol_load_effective": mean_incoming_aerosol_load,
        "forward_burnable_cloud_incoming_aerosol_load_full_window_mean": full_window_incoming_aerosol_load,
        "burnable_cloud_coverage_peak": peak_burn,
        "burnable_cloud_coverage_mean": mean_burn,
        "burnable_cloud_coverage_effective": mean_burn,
        "burnable_to_view_ratio": ratio,
        "coverage_base_score": coverage_base,
        "coverage_score": coverage_base,
        "cloud_material": material_quality,
        "material_reference_quality": MATERIAL_REFERENCE_QUALITY,
        "material_factor": material_factor,
        "material_adjustment": material_delta,
        "incoming_aod_illumination_factor": incoming_aod_illumination,
        "incoming_aod_adjustment": incoming_aod_delta,
        "observer_aod_contrast_factor": observer_aod_contrast,
        "observer_aod_adjustment": observer_aod_delta,
        "burnable_ratio_factor": 1.0,
        "base_quality_index": coverage_base,
        "raw_quality_index": after_ceiling,
        "pre_response_quality_index": pre_response_score,
        "quality_response_scale": QUALITY_RESPONSE_SCALE,
        "quality_response_exponent": QUALITY_RESPONSE_EXPONENT,
        "quality_response_adjustment": final_score - pre_response_score,
        "duration_minutes": duration,
        "duration_factor": duration_multiplier,
        "duration_window_minutes": duration_window_minutes,
        "duration_window_pre_response_index": duration_window_pre_response,
        "duration_adjustment": duration_delta,
        "quality_aod550": local_aod,
        "local_aod550": local_aod,
        "solar_incoming_block_risk_peak": incoming_block_peak,
        "solar_incoming_block_risk_mean": incoming_block_mean,
        "solar_incoming_block_risk_effective": incoming_block_mean,
        "observer_local_cloud_field_amount_peak": float(np.max(local_view)) if local_view.size else 0.0,
        "observer_local_cloud_field_amount_mean": _clip(_time_mean(local_view, offsets)) if local_view.size else 0.0,
        "observer_local_burnable_cloud_field_amount_peak": float(np.max(local_burn)) if local_burn.size else 0.0,
        "observer_local_burnable_cloud_field_amount_mean": _clip(_time_mean(local_burn, offsets)) if local_burn.size else 0.0,
        "observer_local_burnable_cloud_load_peak": float(np.max(local_load)) if local_load.size else 0.0,
        "observer_local_burnable_cloud_load_mean": _clip(_time_mean(local_load, offsets), 0.0, 2.5) if local_load.size else 0.0,
        "observer_local_burnable_cloud_material_load_peak": float(np.max(local_material_load)) if local_material_load.size else 0.0,
        "observer_local_burnable_cloud_material_load_mean": _clip(_time_mean(local_material_load, offsets), 0.0, 2.5) if local_material_load.size else 0.0,
        "observer_column_quality_index": local_quality_index,
        "observer_column_quality_peak": local_quality_peak,
        "observer_column_quality_source": (
            "同一评分公式应用于观测点本地 GFS 云柱，仅作单点/范围诊断；"
            "它不替换完整前方天空的区域质量指数"
            if local_quality_index is not None
            else "未提供观测点本地云柱诊断"
        ),
        "surface_visibility_gate_peak": float(np.max(visible[active])) if np.any(active) else 0.0,
        "brightness_color": float(np.mean(vivid[active])) if np.any(active) else 0.0,
        "quality_limiting_factor": quality_limiting_factor,
        "physically_burnable_steps": float(np.count_nonzero(active)),
        "event_active_cloud_threshold": event_active_threshold,
        "observation_burnability_veto_steps": float(np.count_nonzero(~gate)),
        "quality_coverage_source": (
            f"观测点朝太阳方向 ±90°、{float(analysis_radius_km):.0f} km 半圆内"
            "全部 GFS 云格投影到观测者前方天空，按方位与仰角的立体角计算可见及可烧云占比；"
            "观测点所在水平云格覆盖到天顶，远处云格按近边/远边实际仰角贡献；"
            "每个云底分别追踪太阳入射与云底到观测者的回程光路，逐格积分 CAMS AOD 与"
            "垂直廓线得到的消光系数；入射段决定云底受光，回程段独立修正对比度与饱和度；"
            "再按观测视线由近到远遮挡合成；"
            "云量质量仅统计物理可烧时段，全窗口零值只进入持续时间；"
            "云量只由可见且受光的云底面积决定；云质、入射 AOD、观测 AOD 与持续时间各修正一次；"
            "入射阻挡仅在超过原生可烧/可见互补关系时作为不可烧云上限，不重复扣分；"
            "不选 Top-K、不选代表云、不使用镜头焦段"
        ),
    }
    if has_score_ceiling:
        components.update({
            "observation_score_ceiling": effective_score_ceiling,
            "observation_ceiling_adjustment": observation_ceiling_delta,
        })
    return {"index": float(final_score), "components": components}


__all__ = [
    "ACTIVE_CLOUD_THRESHOLD",
    "CLOUD_AMOUNT_SCORE_POINTS",
    "MAX_SCORE",
    "MIN_OBSERVER_VISIBILITY_FOR_SCORE",
    "MATERIAL_REFERENCE_QUALITY",
    "PHYSICAL_LEDGER_COMPONENT_FIELDS",
    "EMPIRICAL_RESPONSE_COMPONENT_FIELDS",
    "POLICY_DIAGNOSTIC_COMPONENT_FIELDS",
    "OBSERVER_CLOUD_FIELD_COMPONENT_FIELDS",
    "OBSERVER_CLOUD_FIELD_SCORE_CEILING_COMPONENT_FIELDS",
    "OBSERVER_CLOUD_FIELD_SCORE_FORMULA",
    "SCORING_COMPONENT_LAYER_CONTRACT",
    "QUALITY_RESPONSE_EXPONENT",
    "QUALITY_RESPONSE_SCALE",
    "SCORING_SCOPE",
    "calibrated_quality_response",
    "inverse_calibrated_quality_response",
    "duration_adjustment",
    "duration_factor",
    "score_instant_observer_cloud_field_batch",
    "score_observer_cloud_field_batch",
    "score_observer_cloud_field",
]
