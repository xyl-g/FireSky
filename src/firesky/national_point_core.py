from __future__ import annotations

import math

import numpy as np
import struct
from datetime import timedelta, timezone
from numbers import Real
from typing import Any
from zoneinfo import ZoneInfo

from firesky.forecast_characteristics import (
    cloud_evolution_diagnosis,
    cloud_genus_diagnosis,
    color_diagnosis,
    fire_cloud_quality_level,
    score_curve_window,
)
from firesky.geometry import solar_event_time_local
from firesky.forward_field_scoring import (
    MATERIAL_REFERENCE_QUALITY,
    MAX_SCORE,
    OBSERVER_CLOUD_FIELD_SCORE_FORMULA,
    QUALITY_RESPONSE_EXPONENT,
    QUALITY_RESPONSE_SCALE,
    SCORING_SCOPE,
)
from firesky.models import ForecastRequest
from firesky.national_batch import (
    EC_SELECTED_COLUMN_DIAGNOSTIC_SOURCE,
    NATIONAL_POINT_CORE_ALGORITHM,
    NATIONAL_POINT_QUALITY_COMPONENT_FIELDS,
    NATIONAL_POINT_CORE_VERSION,
    NATIONAL_SELECTED_COLUMN_DIAGNOSTIC_SOURCE,
    cloud_optics_metadata_for_model,
    national_event_offsets_minutes,
)
from firesky.time_contract import (
    parse_iso_date,
    parse_utc_iso_datetime,
    parse_zoned_iso_datetime,
)


POINT_CORE_FIELDS = frozenset({
    "version", "algorithm", "target_date", "phenomenon", "model_source",
    "forecast_mode", "point_refinement", "analysis_radius_km", "precision_mode",
    "lat", "lon", "timezone_name", "observer_elevation_asl_m", "anchor_local",
    "cycle_time_utc", "model_cycles", "confidence_score", "confidence_kind",
    "forecast_lead_hours", "forecast_lead_semantics", "quality_index_semantics",
    "cloud_optics", "cloud_geometry_proxy_source", "cloud_geometry_boundary_semantics",
    "cloud_geometry_support_count",
    "cloud_geometry_uncertainty_m", "cloud_geometry_confidence",
    "model_disagreement_score", "model_weights", "quality_index", "quality_level",
    "peak_quality_index", "peak_quality_level", "overall_score",
    "best_offset_minutes", "quality_components",
    "burnable_cloud_coverage_effective", "forward_view_cloud_field_amount_peak",
    "forward_burnable_cloud_field_amount_peak", "forward_burnable_cloud_load_peak",
    "forward_burnable_cloud_load_effective",
    "forward_burnable_cloud_material_load_peak",
    "forward_burnable_cloud_material_load_effective",
    "solar_incoming_block_risk_peak", "forward_burnable_cloud_ratio_peak",
    "burnable_duration_minutes", "dominant_burnable_pressure_family",
    "dominant_burnable_cloud_base_asl_mean_m",
    "dominant_burnable_cloud_top_asl_mean_m",
    "dominant_burnable_target_terrain_asl_mean_m", "convective_risk", "aerosol",
    "steps",
})
POINT_CORE_STEP_FIELDS = frozenset({
    "selected_column_diagnostic_source", "offset_minutes", "valid",
    "instant_quality_index",
    "instant_quality_level", "physically_burnable", "burnable_cloud_coverage",
    "forward_view_cloud_field_amount", "forward_burnable_cloud_field_amount",
    "forward_candidate_cloud_field_amount",
    "forward_target_eligible_cloud_field_amount",
    "forward_solar_geometry_reachable_cloud_field_amount",
    "forward_external_cloud_path_transmitted_field_amount",
    "forward_unoccluded_burn_potential_upper_bound",
    "forward_burnable_cloud_load", "forward_burnable_cloud_material_load",
    "forward_burnable_cloud_incoming_aerosol_load", "solar_incoming_block_risk",
    "forward_high_cloud_amount", "forward_mid_cloud_amount", "forward_low_cloud_amount",
    "forward_high_burnable_amount", "forward_mid_burnable_amount",
    "forward_low_burnable_amount", "observer_local_cloud_field_amount",
    "observer_local_burnable_cloud_field_amount", "observer_local_burnable_cloud_load",
    "observer_local_burnable_cloud_material_load",
    "observer_local_burnable_cloud_incoming_aerosol_load",
    "ground_area_cloud_field_amount", "ground_area_burnable_cloud_field_amount",
    "ground_area_burnable_cloud_load", "solar_elevation_deg", "solar_azimuth_deg",
    "target_deck_connected",
    "target_deck_attenuated_fraction", "target_deep_risk", "cloud_optical_depth",
    "peak_blocking", "clear_gap_count", "observer_return_optical_depth",
    "observer_return_transmission", "observer_local_return_optical_depth",
    "observer_local_return_transmission", "cloud_geometry_proxy_source",
    "cloud_geometry_support_count", "cloud_geometry_uncertainty_m",
    "cloud_geometry_confidence", "forward_burnable_cloud_ratio", "brightness_color",
    "cloud_material_score", "selected_column_cloud_material_class_code",
    "quality_aod550",
    "dominant_burnable_pressure_family", "dominant_burnable_cloud_base_asl_mean_m",
    "dominant_burnable_cloud_top_asl_mean_m",
    "dominant_burnable_target_terrain_asl_mean_m", "burn_score", "intensity_score",
    "extent_score", "vividness_score", "visibility_score",
    "observer_mid_blocker_opacity", "observer_low_blocker_opacity", "selected_regime",
    "scene_scores", "local_sky_score", "remote_sky_score", "convective_risk",
    "color_diagnosis",
})
POINT_CORE_OPTIONAL_STEP_FIELDS = frozenset({
    "cloud_phase_proxy", "cloud_genus_proxy",
})
POINT_CORE_SCENE_FIELDS = frozenset({
    "clear", "forward_cloud_field", "burnable_cloud_field",
    "solar_incoming_blocked_field",
})
POINT_CORE_AEROSOL_FIELDS = frozenset({"aod550", "source", "quality"})
POINT_CORE_FAMILIES = frozenset({"high", "middle", "low"})
POINT_CORE_COMPONENT_TEXT_FIELDS = frozenset({
    "observer_column_quality_source",
    "quality_limiting_factor",
    "quality_coverage_source",
})
POINT_CORE_COMPONENT_UNIT_FIELDS = frozenset({
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
    "burnable_cloud_coverage_peak",
    "burnable_cloud_coverage_mean",
    "burnable_cloud_coverage_effective",
    "burnable_to_view_ratio",
    "cloud_material",
    "material_factor",
    "incoming_aod_illumination_factor",
    "observer_aod_contrast_factor",
    "solar_incoming_block_risk_peak",
    "solar_incoming_block_risk_mean",
    "solar_incoming_block_risk_effective",
    "solar_incoming_block_risk_at_best_cloud",
    "observer_local_cloud_field_amount_peak",
    "observer_local_cloud_field_amount_mean",
    "observer_local_burnable_cloud_field_amount_peak",
    "observer_local_burnable_cloud_field_amount_mean",
    "surface_visibility_gate_peak",
    "brightness_color",
    "event_active_cloud_threshold",
    "observer_return_transmission_mean",
    "observer_local_return_transmission_mean",
    "ground_area_view_amount_mean",
    "ground_area_burnable_amount_mean",
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
})
POINT_CORE_COMPONENT_SCORE_FIELDS = frozenset({
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
    "coverage_base_score",
    "coverage_score",
    "base_quality_index",
    "raw_quality_index",
    "pre_response_quality_index",
    "duration_window_pre_response_index",
    "observer_local_burnable_cloud_load_peak",
    "observer_local_burnable_cloud_load_mean",
    "observer_local_burnable_cloud_material_load_peak",
    "observer_local_burnable_cloud_material_load_mean",
    "ground_area_burnable_load_mean",
    "observer_column_quality_index",
    "observer_column_quality_peak",
})
POINT_CORE_COMPONENT_SIGNED_SCORE_FIELDS = frozenset({
    "material_adjustment",
    "incoming_aod_adjustment",
    "observer_aod_adjustment",
    "quality_response_adjustment",
    "duration_adjustment",
})
POINT_CORE_COMPONENT_NONNEGATIVE_FIELDS = frozenset({
    "duration_minutes",
    "duration_window_minutes",
    "quality_aod550",
    "local_aod550",
    "physically_burnable_steps",
    "observation_burnability_veto_steps",
    "observer_return_optical_depth_mean",
    "observer_local_return_optical_depth_mean",
})
POINT_CORE_COMPONENT_NULLABLE_SCORE_FIELDS = frozenset()
POINT_CORE_COMPONENT_EXACT_FIELDS = frozenset({
    "scoring_scope",
    "score_formula",
    "score_max",
    "forward_sector_half_angle_deg",
    "forward_field_radius_km",
    "material_reference_quality",
    "burnable_ratio_factor",
    "quality_response_scale",
    "quality_response_exponent",
    "duration_factor",
    "cloud_optics_source",
    "effective_radius_source",
    "cloud_optical_depth_source",
    "cloud_opacity_proxy_source",
    "cams_profile_normalization",
    "visual_response_mapping",
    "unavailable_spectral_quantities",
    "observer_return_model",
})
_POINT_CORE_VALIDATED_COMPONENT_FIELDS = (
    POINT_CORE_COMPONENT_TEXT_FIELDS
    | POINT_CORE_COMPONENT_UNIT_FIELDS
    | POINT_CORE_COMPONENT_SCORE_FIELDS
    | POINT_CORE_COMPONENT_SIGNED_SCORE_FIELDS
    | POINT_CORE_COMPONENT_NONNEGATIVE_FIELDS
    | POINT_CORE_COMPONENT_NULLABLE_SCORE_FIELDS
    | POINT_CORE_COMPONENT_EXACT_FIELDS
)
assert _POINT_CORE_VALIDATED_COMPONENT_FIELDS == NATIONAL_POINT_QUALITY_COMPONENT_FIELDS, (
    _POINT_CORE_VALIDATED_COMPONENT_FIELDS - NATIONAL_POINT_QUALITY_COMPONENT_FIELDS,
    NATIONAL_POINT_QUALITY_COMPONENT_FIELDS - _POINT_CORE_VALIDATED_COMPONENT_FIELDS,
)
POINT_CORE_STEP_UNIT_FIELDS = frozenset({
    "burn_score", "intensity_score", "extent_score", "vividness_score",
    "visibility_score", "burnable_cloud_coverage", "forward_view_cloud_field_amount",
    "forward_burnable_cloud_field_amount", "forward_burnable_cloud_ratio",
    "forward_candidate_cloud_field_amount",
    "forward_target_eligible_cloud_field_amount",
    "forward_solar_geometry_reachable_cloud_field_amount",
    "forward_external_cloud_path_transmitted_field_amount",
    "forward_unoccluded_burn_potential_upper_bound",
    "solar_incoming_block_risk",
    "forward_high_cloud_amount", "forward_mid_cloud_amount", "forward_low_cloud_amount",
    "forward_high_burnable_amount", "forward_mid_burnable_amount",
    "forward_low_burnable_amount", "observer_local_cloud_field_amount",
    "observer_local_burnable_cloud_field_amount", "ground_area_cloud_field_amount",
    "ground_area_burnable_cloud_field_amount", "target_deck_connected",
    "target_deck_attenuated_fraction", "target_deep_risk", "peak_blocking",
    "observer_return_transmission", "observer_local_return_transmission",
    "cloud_geometry_confidence", "brightness_color", "cloud_material_score",
    "observer_mid_blocker_opacity",
    "observer_low_blocker_opacity", "local_sky_score", "remote_sky_score",
    "convective_risk",
})
POINT_CORE_STEP_NONNEGATIVE_FIELDS = frozenset({
    "cloud_optical_depth", "clear_gap_count",
    "observer_return_optical_depth", "observer_local_return_optical_depth",
    "cloud_geometry_uncertainty_m", "quality_aod550",
})
POINT_CORE_STEP_SCORE_FIELDS = frozenset({
    "forward_burnable_cloud_load", "forward_burnable_cloud_material_load",
    "forward_burnable_cloud_incoming_aerosol_load",
    "observer_local_burnable_cloud_load", "observer_local_burnable_cloud_material_load",
    "observer_local_burnable_cloud_incoming_aerosol_load",
    "ground_area_burnable_cloud_load",
})
POINT_CORE_INVALID_ZERO_FIELDS = frozenset({
    "instant_quality_index",
    "burn_score",
    "intensity_score",
    "extent_score",
    "vividness_score",
    "visibility_score",
    "burnable_cloud_coverage",
    "forward_view_cloud_field_amount",
    "forward_burnable_cloud_field_amount",
    "forward_candidate_cloud_field_amount",
    "forward_target_eligible_cloud_field_amount",
    "forward_solar_geometry_reachable_cloud_field_amount",
    "forward_external_cloud_path_transmitted_field_amount",
    "forward_unoccluded_burn_potential_upper_bound",
    "forward_burnable_cloud_load",
    "forward_burnable_cloud_material_load",
    "forward_burnable_cloud_incoming_aerosol_load",
    "solar_incoming_block_risk",
    "forward_high_cloud_amount",
    "forward_mid_cloud_amount",
    "forward_low_cloud_amount",
    "forward_high_burnable_amount",
    "forward_mid_burnable_amount",
    "forward_low_burnable_amount",
    "observer_local_cloud_field_amount",
    "observer_local_burnable_cloud_field_amount",
    "observer_local_burnable_cloud_load",
    "observer_local_burnable_cloud_material_load",
    "observer_local_burnable_cloud_incoming_aerosol_load",
    "ground_area_cloud_field_amount",
    "ground_area_burnable_cloud_field_amount",
    "ground_area_burnable_cloud_load",
    "target_deck_connected",
    "target_deck_attenuated_fraction",
    "target_deep_risk",
    "cloud_optical_depth",
    "peak_blocking",
    "clear_gap_count",
    "observer_return_optical_depth",
    "observer_return_transmission",
    "observer_local_return_optical_depth",
    "observer_local_return_transmission",
    "cloud_geometry_uncertainty_m",
    "cloud_geometry_confidence",
    "forward_burnable_cloud_ratio",
    "brightness_color",
    "cloud_material_score",
    "observer_mid_blocker_opacity",
    "observer_low_blocker_opacity",
    "local_sky_score",
    "remote_sky_score",
    "convective_risk",
})


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _range(value: Any, field: str, low: float, high: float) -> float:
    result = _number(value, field)
    if result < low or result > high:
        raise ValueError(f"{field} must be between {low} and {high}")
    return result


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _same_number(left: Any, right: Any, field: str) -> None:
    if _number(left, field) != _number(right, f"{field}.source"):
        raise ValueError(f"{field} does not match its authoritative source")


def _float32(value: Any, field: str) -> float:
    number = _number(value, field)
    return struct.unpack("<f", struct.pack("<f", number))[0]


def _geometry(family: Any, base: Any, top: Any, terrain: Any, field: str) -> None:
    if family is None:
        if any(value is not None for value in (base, top, terrain)):
            raise ValueError(f"{field} cannot carry geometry without a pressure family")
        return
    if family not in POINT_CORE_FAMILIES:
        raise ValueError(f"{field} has an invalid pressure family")
    base_value = _number(base, f"{field}.base_asl_m")
    top_value = _number(top, f"{field}.top_asl_m")
    _number(terrain, f"{field}.terrain_asl_m")
    if top_value < base_value:
        raise ValueError(f"{field} top cannot be below base")


def validate_national_point_core(
    core: object,
    request: ForecastRequest,
) -> dict[str, Any]:
    if not isinstance(core, dict) or set(core) != POINT_CORE_FIELDS:
        raise ValueError("point core fields do not match the current contract")
    if core["version"] != NATIONAL_POINT_CORE_VERSION:
        raise ValueError("point core version is not current")
    if core["algorithm"] != NATIONAL_POINT_CORE_ALGORITHM:
        raise ValueError("point core algorithm is not current")
    if core["forecast_mode"] != "model" or core["forecast_mode"] != request.forecast_mode:
        raise ValueError("point core forecast mode does not match the request")
    if core["phenomenon"] != request.phenomenon:
        raise ValueError("point core phenomenon does not match the request")
    if core["model_source"] != request.model_source:
        raise ValueError("point core model does not match the request")
    target_date = parse_iso_date(core["target_date"], "point_core.target_date")
    if target_date != request.target_date:
        raise ValueError("point core target date does not match the request")
    lat = _range(core["lat"], "point_core.lat", -90.0, 90.0)
    lon = _range(core["lon"], "point_core.lon", -180.0, 180.0)
    if abs(lat - request.point.lat) > 1e-9 or abs(lon - request.point.lon) > 1e-9:
        raise ValueError("point core coordinates do not match the request")
    elevation = _number(
        core["observer_elevation_asl_m"], "point_core.observer_elevation_asl_m"
    )
    if abs(elevation - request.point.elevation_m) > 1e-6:
        raise ValueError("point core observer elevation does not match the request")
    if core["timezone_name"] != request.timezone_name:
        raise ValueError("point core timezone does not match the request")
    anchor = parse_zoned_iso_datetime(core["anchor_local"], "point_core.anchor_local")
    if anchor.date() != target_date:
        raise ValueError("point core anchor does not match target_date")
    expected_timezone = ZoneInfo(request.timezone_name)
    expected_offset = anchor.astimezone(expected_timezone).utcoffset()
    if anchor.utcoffset() != expected_offset:
        raise ValueError("point core anchor UTC offset does not match its timezone")
    expected_anchor = solar_event_time_local(
        target_date,
        request.point,
        request.timezone_name,
        request.phenomenon,
    )
    if abs(anchor.timestamp() - expected_anchor.timestamp()) >= 1.0:
        raise ValueError("point core anchor does not match the scalar solar event")
    cycle = parse_utc_iso_datetime(core["cycle_time_utc"], "point_core.cycle_time_utc")
    if request.gfs_cycle_override is not None and cycle != request.gfs_cycle_override.astimezone(timezone.utc):
        raise ValueError("point core cycle does not match the pinned request cycle")
    if core["point_refinement"] != "raytraced-sky-v4-cpu":
        raise ValueError("point core refinement mode is not current")
    if core["precision_mode"] != "raytraced-sky-v4-cpu":
        raise ValueError("point core precision mode is not current")
    radius = _number(core["analysis_radius_km"], "point_core.analysis_radius_km")
    if radius != float(request.radius_km):
        raise ValueError("point core analysis radius does not match the request")

    model_cycles = core["model_cycles"]
    model_weights = core["model_weights"]
    if not isinstance(model_cycles, dict) or set(model_cycles) != {request.model_source}:
        raise ValueError("point core model_cycles does not match the request")
    if not isinstance(model_weights, dict) or set(model_weights) != set(model_cycles):
        raise ValueError("point core model_weights does not match model_cycles")
    if parse_utc_iso_datetime(
        model_cycles[request.model_source], "point_core.model_cycles"
    ) != cycle:
        raise ValueError("point core model cycle is inconsistent")
    if _number(model_weights[request.model_source], "point_core.model_weight") != 1.0:
        raise ValueError("point core model weight must equal one")

    for field in ("confidence_score", "cloud_geometry_confidence", "model_disagreement_score", "overall_score", "convective_risk"):
        _range(core[field], f"point_core.{field}", 0.0, 1.0)
    for field in ("confidence_kind", "forecast_lead_semantics", "quality_index_semantics", "cloud_geometry_boundary_semantics"):
        _text(core[field], f"point_core.{field}")
    if core["forecast_lead_hours"] is not None:
        if _number(core["forecast_lead_hours"], "point_core.forecast_lead_hours") < 0.0:
            raise ValueError("point core forecast lead cannot be negative")
    if _number(
        core["model_disagreement_score"], "point_core.model_disagreement_score"
    ) != 0.0:
        raise ValueError("single-model point core disagreement must equal zero")
    for field in ("quality_index", "peak_quality_index"):
        _range(core[field], f"point_core.{field}", 0.0, 2.5)
    if core["quality_level"] != fire_cloud_quality_level(float(core["quality_index"])):
        raise ValueError("point core quality level does not match quality index")
    if core["peak_quality_level"] != fire_cloud_quality_level(
        float(core["peak_quality_index"])
    ):
        raise ValueError("point core peak quality level does not match peak index")
    _text(core["cloud_geometry_proxy_source"], "point_core.cloud_geometry_proxy_source")
    support_count = core["cloud_geometry_support_count"]
    if isinstance(support_count, bool) or not isinstance(support_count, int) or support_count < -1:
        raise ValueError("point core cloud geometry support count is invalid")
    if _number(core["cloud_geometry_uncertainty_m"], "point_core.cloud_geometry_uncertainty_m") < 0.0:
        raise ValueError("point core cloud geometry uncertainty cannot be negative")
    expected_cloud_optics = cloud_optics_metadata_for_model(request.model_source)
    if core["cloud_optics"] != expected_cloud_optics:
        raise ValueError("point core cloud optics metadata is not current")

    components = core["quality_components"]
    if (
        not isinstance(components, dict)
        or set(components) != NATIONAL_POINT_QUALITY_COMPONENT_FIELDS
    ):
        raise ValueError("point core quality components do not match the exact contract")
    if components["scoring_scope"] != SCORING_SCOPE:
        raise ValueError("point core scoring scope is not current")
    if components["score_formula"] != OBSERVER_CLOUD_FIELD_SCORE_FORMULA:
        raise ValueError("point core score formula is not current")
    if components["observer_return_model"] != (
        "raw_beer_lambert_diagnostic; no cloud/sky radiance or perception mapping"
    ):
        raise ValueError("point core observer return model is not current")
    for field in POINT_CORE_COMPONENT_TEXT_FIELDS:
        _text(components[field], f"point_core.quality_components.{field}")
    for field in POINT_CORE_COMPONENT_UNIT_FIELDS:
        _range(components[field], f"point_core.quality_components.{field}", 0.0, 1.0)
    for field in POINT_CORE_COMPONENT_SCORE_FIELDS:
        _range(components[field], f"point_core.quality_components.{field}", 0.0, MAX_SCORE)
    for field in POINT_CORE_COMPONENT_SIGNED_SCORE_FIELDS:
        _range(
            components[field],
            f"point_core.quality_components.{field}",
            -MAX_SCORE,
            MAX_SCORE,
        )
    for field in POINT_CORE_COMPONENT_NONNEGATIVE_FIELDS:
        if _number(components[field], f"point_core.quality_components.{field}") < 0.0:
            raise ValueError(f"point core quality component {field} cannot be negative")
    for field in POINT_CORE_COMPONENT_NULLABLE_SCORE_FIELDS:
        if components[field] is not None:
            _range(
                components[field],
                f"point_core.quality_components.{field}",
                0.0,
                MAX_SCORE,
            )
    exact_numbers = {
        "score_max": MAX_SCORE,
        "forward_sector_half_angle_deg": 90.0,
        "forward_field_radius_km": radius,
        "material_reference_quality": MATERIAL_REFERENCE_QUALITY,
        "burnable_ratio_factor": 1.0,
        "quality_response_scale": QUALITY_RESPONSE_SCALE,
        "quality_response_exponent": QUALITY_RESPONSE_EXPONENT,
    }
    for field, expected in exact_numbers.items():
        if _number(components[field], f"point_core.quality_components.{field}") != expected:
            raise ValueError(f"point core quality component {field} is not current")
    _range(
        components["duration_factor"],
        "point_core.quality_components.duration_factor",
        0.0,
        1.1,
    )
    for field, expected in expected_cloud_optics.items():
        if components[field] != expected:
            raise ValueError(f"point core quality component {field} is not current")
    for field in ("physically_burnable_steps", "observation_burnability_veto_steps"):
        value = _number(components[field], f"point_core.quality_components.{field}")
        if value != math.floor(value):
            raise ValueError(f"point core quality component {field} must be integral")
    if _number(
        components["observation_burnability_veto_steps"],
        "point_core.quality_components.observation_burnability_veto_steps",
    ) != 0.0:
        raise ValueError("native point core cannot contain observation burnability vetoes")
    for field in (
        "burnable_cloud_coverage_effective", "forward_view_cloud_field_amount_peak",
        "forward_burnable_cloud_field_amount_peak", "solar_incoming_block_risk_peak",
        "forward_burnable_cloud_ratio_peak",
    ):
        _range(core[field], f"point_core.{field}", 0.0, 1.0)
    for field in (
        "forward_burnable_cloud_load_peak", "forward_burnable_cloud_load_effective",
        "forward_burnable_cloud_material_load_peak",
        "forward_burnable_cloud_material_load_effective", "burnable_duration_minutes",
    ):
        if _number(core[field], f"point_core.{field}") < 0.0:
            raise ValueError(f"point_core.{field} cannot be negative")
    duplicate_component_fields = {
        "burnable_cloud_coverage_effective": "burnable_cloud_coverage_effective",
        "forward_view_cloud_field_amount_peak": "forward_view_cloud_field_amount_peak",
        "forward_burnable_cloud_field_amount_peak": (
            "forward_burnable_cloud_field_amount_peak"
        ),
        "forward_burnable_cloud_load_peak": "forward_burnable_cloud_load_peak",
        "forward_burnable_cloud_load_effective": (
            "forward_burnable_cloud_load_effective"
        ),
        "forward_burnable_cloud_material_load_peak": (
            "forward_burnable_cloud_material_load_peak"
        ),
        "forward_burnable_cloud_material_load_effective": (
            "forward_burnable_cloud_material_load_effective"
        ),
        "solar_incoming_block_risk_peak": "solar_incoming_block_risk_peak",
        "forward_burnable_cloud_ratio_peak": "forward_burnable_cloud_ratio_peak",
        "burnable_duration_minutes": "duration_minutes",
    }
    for root_field, component_field in duplicate_component_fields.items():
        _same_number(
            core[root_field],
            components[component_field],
            f"point_core.{root_field}",
        )
    _geometry(
        core["dominant_burnable_pressure_family"],
        core["dominant_burnable_cloud_base_asl_mean_m"],
        core["dominant_burnable_cloud_top_asl_mean_m"],
        core["dominant_burnable_target_terrain_asl_mean_m"],
        "point_core.dominant_burnable_cloud",
    )
    aerosol = core["aerosol"]
    if not isinstance(aerosol, dict) or set(aerosol) != POINT_CORE_AEROSOL_FIELDS:
        raise ValueError("point core aerosol fields are invalid")
    if _number(aerosol["aod550"], "point_core.aerosol.aod550") < 0.0:
        raise ValueError("point core aerosol AOD cannot be negative")
    _text(aerosol["source"], "point_core.aerosol.source")
    _text(aerosol["quality"], "point_core.aerosol.quality")

    steps = core["steps"]
    if not isinstance(steps, list) or not steps:
        raise ValueError("point core steps must be a non-empty array")
    expected_offsets = national_event_offsets_minutes(request.phenomenon)
    if len(steps) != len(expected_offsets):
        raise ValueError("point core steps do not cover the canonical event timeline")
    offsets: set[float] = set()
    previous_offset: float | None = None
    for index, step in enumerate(steps):
        prefix = f"point_core.steps[{index}]"
        if (
            not isinstance(step, dict)
            or not POINT_CORE_STEP_FIELDS.issubset(step)
            or set(step) - POINT_CORE_STEP_FIELDS - POINT_CORE_OPTIONAL_STEP_FIELDS
        ):
            raise ValueError(f"{prefix} fields are invalid")
        expected_selected_source = {
            "gfs": NATIONAL_SELECTED_COLUMN_DIAGNOSTIC_SOURCE,
            "ec": EC_SELECTED_COLUMN_DIAGNOSTIC_SOURCE,
        }.get(request.model_source)
        if step["selected_column_diagnostic_source"] != expected_selected_source:
            raise ValueError(f"{prefix} diagnostic source is not current")
        offset = _number(step["offset_minutes"], f"{prefix}.offset_minutes")
        if offset != float(expected_offsets[index]):
            raise ValueError(f"{prefix}.offset_minutes is not canonical")
        if previous_offset is not None and offset <= previous_offset:
            raise ValueError("point core offsets must be strictly increasing")
        previous_offset = offset
        offsets.add(offset)
        if not isinstance(step["valid"], bool):
            raise ValueError(f"{prefix}.valid must be boolean")
        instant = _range(step["instant_quality_index"], f"{prefix}.instant_quality_index", 0.0, 2.5)
        if not math.isclose(_number(step["burn_score"], f"{prefix}.burn_score"), instant / 2.5, abs_tol=1e-9, rel_tol=0.0):
            raise ValueError(f"{prefix}.burn_score must equal instant_quality_index / 2.5")
        if step["instant_quality_level"] != fire_cloud_quality_level(instant):
            raise ValueError(f"{prefix}.instant_quality_level does not match its index")
        if not isinstance(step["physically_burnable"], bool):
            raise ValueError(f"{prefix}.physically_burnable must be boolean")
        if not step["valid"]:
            if step["physically_burnable"]:
                raise ValueError(f"{prefix} invalid sample carries active quality")
            for field in POINT_CORE_INVALID_ZERO_FIELDS:
                if _number(step[field], f"{prefix}.{field}") != 0.0:
                    raise ValueError(
                        f"{prefix} invalid sample carries nonzero {field}"
                    )
            if (
                step["dominant_burnable_pressure_family"] is not None
                or step["selected_column_cloud_material_class_code"] != 0
                or step["cloud_geometry_support_count"] != -1
            ):
                raise ValueError(f"{prefix} invalid sample carries cloud structure")
        for field in POINT_CORE_STEP_UNIT_FIELDS:
            _range(step[field], f"{prefix}.{field}", 0.0, 1.0)
        ledger_values = (
            float(step["forward_burnable_cloud_field_amount"]),
            float(step["forward_external_cloud_path_transmitted_field_amount"]),
            float(step["forward_solar_geometry_reachable_cloud_field_amount"]),
            float(step["forward_target_eligible_cloud_field_amount"]),
            float(step["forward_view_cloud_field_amount"]),
            float(step["forward_candidate_cloud_field_amount"]),
        )
        if step["valid"] and any(
            left > right + 1e-6 for left, right in zip(ledger_values, ledger_values[1:])
        ):
            raise ValueError(
                f"{prefix} violates candidate>=view>=eligible>=geometry>=path>=burn"
            )
        if step["valid"] and float(
            step["forward_unoccluded_burn_potential_upper_bound"]
        ) + 1e-6 < ledger_values[0]:
            raise ValueError(f"{prefix} violates unoccluded upper bound>=burn")
        for field in POINT_CORE_STEP_NONNEGATIVE_FIELDS:
            if _number(step[field], f"{prefix}.{field}") < 0.0:
                raise ValueError(f"{prefix}.{field} cannot be negative")
        for field in POINT_CORE_STEP_SCORE_FIELDS:
            _range(step[field], f"{prefix}.{field}", 0.0, MAX_SCORE)
        _range(step["solar_elevation_deg"], f"{prefix}.solar_elevation_deg", -90.0, 90.0)
        azimuth = _range(step["solar_azimuth_deg"], f"{prefix}.solar_azimuth_deg", 0.0, 360.0)
        if azimuth >= 360.0:
            raise ValueError(f"{prefix}.solar_azimuth_deg must be below 360")
        for field, maximum in (
            ("cloud_geometry_support_count", None),
            ("selected_column_cloud_material_class_code", 8),
        ):
            value = step[field]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{prefix}.{field} must be an integer")
            if value < (-1 if field == "cloud_geometry_support_count" else 0) or (
                maximum is not None and value > maximum
            ):
                raise ValueError(f"{prefix}.{field} is out of range")
        color = step["color_diagnosis"]
        color_fields = {"type", "confidence", "cloud_phase", "source", "inputs"}
        input_fields = {
            "quality_strength", "brightness_color", "solar_elevation_deg", "aod550",
            "solar_incoming_illumination_score", "deep_cloud_risk", "cloud_phase",
        }
        if not isinstance(color, dict) or set(color) != color_fields:
            raise ValueError(f"{prefix}.color_diagnosis has invalid fields")
        inputs = color["inputs"]
        if not isinstance(inputs, dict) or set(inputs) != input_fields:
            raise ValueError(f"{prefix}.color_diagnosis.inputs has invalid fields")
        _same_number(inputs["quality_strength"], instant / MAX_SCORE, f"{prefix}.color.quality")
        _same_number(inputs["brightness_color"], step["brightness_color"], f"{prefix}.color.brightness")
        _same_number(inputs["solar_elevation_deg"], step["solar_elevation_deg"], f"{prefix}.color.solar_elevation")
        _same_number(inputs["aod550"], step["quality_aod550"], f"{prefix}.color.aod550")
        _same_number(
            inputs["deep_cloud_risk"],
            step["convective_risk"],
            f"{prefix}.color.deep_cloud_risk",
        )
        _range(
            inputs["solar_incoming_illumination_score"],
            f"{prefix}.color.solar_incoming_illumination_score",
            0.0,
            1.0,
        )
        if (
            not step["valid"]
            and _number(
                inputs["solar_incoming_illumination_score"],
                f"{prefix}.color.solar_incoming_illumination_score",
            )
            != 0.0
        ):
            raise ValueError(f"{prefix} invalid sample carries color illumination")
        phase_label = inputs["cloud_phase"]
        if phase_label not in {"unknown", "ice", "water", "mixed"}:
            raise ValueError(f"{prefix}.color_diagnosis cloud phase is invalid")
        if color["cloud_phase"] != phase_label:
            raise ValueError(f"{prefix}.color_diagnosis cloud phase is not authoritative")
        phase_proxy = step.get("cloud_phase_proxy")
        if phase_proxy is not None:
            if not isinstance(phase_proxy, dict) or not {
                "label", "ice_fraction", "confidence", "source"
            } == set(phase_proxy):
                raise ValueError(f"{prefix}.cloud_phase_proxy is invalid")
            if phase_proxy["label"] != phase_label:
                raise ValueError(f"{prefix}.cloud_phase_proxy disagrees with color")
            _range(phase_proxy["confidence"], f"{prefix}.cloud_phase_proxy.confidence", 0.0, 1.0)
            if phase_proxy["ice_fraction"] is not None:
                _range(phase_proxy["ice_fraction"], f"{prefix}.cloud_phase_proxy.ice_fraction", 0.0, 1.0)
            _text(phase_proxy["source"], f"{prefix}.cloud_phase_proxy.source")
        genus_proxy = step.get("cloud_genus_proxy")
        if genus_proxy is not None:
            if not isinstance(genus_proxy, dict) or not {
                "label", "status", "confidence", "scoring_effect",
                "candidate_scores", "compatible_genera", "source",
                "model_source", "evidence",
            } == set(genus_proxy):
                raise ValueError(f"{prefix}.cloud_genus_proxy is invalid")
            if genus_proxy["status"] != "proxy" or genus_proxy["scoring_effect"] is not False:
                raise ValueError(f"{prefix}.cloud_genus_proxy is not display-only")
            _text(genus_proxy["label"], f"{prefix}.cloud_genus_proxy.label")
            _range(genus_proxy["confidence"], f"{prefix}.cloud_genus_proxy.confidence", 0.0, 1.0)
            if not isinstance(genus_proxy["candidate_scores"], dict):
                raise ValueError(f"{prefix}.cloud_genus_proxy scores are invalid")
            for genus, score in genus_proxy["candidate_scores"].items():
                _text(genus, f"{prefix}.cloud_genus_proxy.genus")
                _range(score, f"{prefix}.cloud_genus_proxy.score", 0.0, 1.0)
            if not isinstance(genus_proxy["compatible_genera"], list):
                raise ValueError(f"{prefix}.cloud_genus_proxy compatible genera are invalid")
            _text(genus_proxy["source"], f"{prefix}.cloud_genus_proxy.source")
            _text(genus_proxy["model_source"], f"{prefix}.cloud_genus_proxy.model_source")
        expected_color = color_diagnosis(
            quality_score=float(inputs["quality_strength"]),
            vividness_score=float(inputs["brightness_color"]),
            solar_elevation_deg=float(inputs["solar_elevation_deg"]),
            aod550=float(inputs["aod550"]),
            solar_incoming_illumination_score=float(
                inputs["solar_incoming_illumination_score"]
            ),
            deep_cloud_risk=float(inputs["deep_cloud_risk"]),
            cloud_phase=phase_label,
        )
        if color != expected_color:
            raise ValueError(f"{prefix}.color_diagnosis is not authoritative")
        _text(step["cloud_geometry_proxy_source"], f"{prefix}.cloud_geometry_proxy_source")
        view_amount = float(step["forward_view_cloud_field_amount"])
        burn_amount = float(step["forward_burnable_cloud_field_amount"])
        incoming_block = float(step["solar_incoming_block_risk"])
        expected_regime = (
            (
                "clear"
                if view_amount < 0.01
                else (
                    "solar_incoming_blocked_field"
                    if incoming_block >= 0.55 and burn_amount < view_amount * 0.55
                    else "forward_cloud_field"
                )
            )
            if step["valid"]
            else "invalid"
        )
        if step["selected_regime"] != expected_regime:
            raise ValueError(f"{prefix}.selected_regime does not match the cloud field")
        scores = step["scene_scores"]
        if not isinstance(scores, dict) or set(scores) != POINT_CORE_SCENE_FIELDS:
            raise ValueError(f"{prefix}.scene_scores is invalid")
        for field, value in scores.items():
            _range(value, f"{prefix}.scene_scores.{field}", 0.0, 1.0)
        expected_scene_scores = (
            {
                "clear": 1.0 - view_amount,
                "forward_cloud_field": view_amount,
                "burnable_cloud_field": burn_amount,
                "solar_incoming_blocked_field": incoming_block,
            }
            if step["valid"]
            else {field: 0.0 for field in POINT_CORE_SCENE_FIELDS}
        )
        for field, expected in expected_scene_scores.items():
            _same_number(scores[field], expected, f"{prefix}.scene_scores.{field}")
        _same_number(
            step["local_sky_score"],
            1.0 - incoming_block if step["valid"] else 0.0,
            f"{prefix}.local_sky_score",
        )
        _same_number(
            step["remote_sky_score"],
            burn_amount,
            f"{prefix}.remote_sky_score",
        )
        _geometry(
            step["dominant_burnable_pressure_family"],
            step["dominant_burnable_cloud_base_asl_mean_m"],
            step["dominant_burnable_cloud_top_asl_mean_m"],
            step["dominant_burnable_target_terrain_asl_mean_m"],
            f"{prefix}.dominant_burnable_cloud",
        )
    valid_steps = [step for step in steps if step["valid"]]
    if not valid_steps:
        raise ValueError("point core timeline must contain at least one valid sample")
    burnable_steps = [
        step for step in valid_steps if step["physically_burnable"]
    ]
    expected_best = (
        max(
            burnable_steps,
            key=lambda step: (
                _float32(step["instant_quality_index"], "point_core.best.instant"),
                _float32(step["burnable_cloud_coverage"], "point_core.best.coverage"),
                _float32(step["forward_burnable_cloud_load"], "point_core.best.load"),
            ),
        )
        if burnable_steps
        else None
    )
    best_offset = core["best_offset_minutes"]
    if expected_best is None:
        if best_offset is not None:
            raise ValueError("point core carries a best offset without a burnable step")
        if float(core["peak_quality_index"]) != 0.0:
            raise ValueError("point core peak quality must be zero without a best step")
        if core["dominant_burnable_pressure_family"] is not None:
            raise ValueError("point core carries a dominant family without a best step")
        if float(core["convective_risk"]) != 0.0:
            raise ValueError("point core convective risk must be zero without a best step")
    else:
        expected_offset = float(expected_best["offset_minutes"])
        best_offset_value = _number(best_offset, "point_core.best_offset_minutes")
        if best_offset_value != expected_offset or best_offset_value not in offsets:
            raise ValueError("point core best offset does not match the exact tie-break")
        expected_peak = _float32(
            expected_best["instant_quality_index"], "point_core.best.instant"
        )
        if float(core["peak_quality_index"]) != expected_peak:
            raise ValueError("point core peak quality does not match best step")
        for root_field in (
            "dominant_burnable_pressure_family",
            "dominant_burnable_cloud_base_asl_mean_m",
            "dominant_burnable_cloud_top_asl_mean_m",
            "dominant_burnable_target_terrain_asl_mean_m",
        ):
            if core[root_field] != expected_best[root_field]:
                raise ValueError(f"point core {root_field} does not match best step")
        _same_number(
            core["convective_risk"],
            expected_best["convective_risk"],
            "point_core.convective_risk",
        )
        if core["cloud_geometry_proxy_source"] != expected_best["cloud_geometry_proxy_source"]:
            raise ValueError("point core cloud geometry source does not match best step")
        if core["cloud_geometry_support_count"] != expected_best[
            "cloud_geometry_support_count"
        ]:
            raise ValueError("point core cloud geometry support does not match best step")
        _same_number(
            core["cloud_geometry_uncertainty_m"],
            expected_best["cloud_geometry_uncertainty_m"],
            "point_core.cloud_geometry_uncertainty_m",
        )
        _same_number(
            core["cloud_geometry_confidence"],
            expected_best["cloud_geometry_confidence"],
            "point_core.cloud_geometry_confidence",
        )
    active_threshold = float(components["event_active_cloud_threshold"])
    # Compare in the f32 domain the coverage values are published in; the
    # generator's count uses the same comparison (see
    # build_national_frame_payloads), so both sides agree exactly.
    active_threshold_f32 = np.float32(active_threshold)
    expected_active_steps = sum(
        step["valid"] and np.float32(step["burnable_cloud_coverage"]) >= active_threshold_f32
        for step in steps
    )
    if float(components["physically_burnable_steps"]) != float(expected_active_steps):
        raise ValueError("point core physically burnable step count is inconsistent")
    expected_overall = (
        sum(float(step["burn_score"]) for step in valid_steps) / len(valid_steps)
    )
    if not math.isclose(float(core["overall_score"]), expected_overall, abs_tol=1e-7, rel_tol=0.0):
        raise ValueError("point core overall score does not match normalized steps")
    return core


def materialize_national_point_core(
    request: ForecastRequest,
    core: object,
) -> dict[str, Any]:
    current = validate_national_point_core(core, request)
    anchor = parse_zoned_iso_datetime(current["anchor_local"], "point_core.anchor_local")
    observer_elevation = float(current["observer_elevation_asl_m"])
    steps: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    for raw_step in current["steps"]:
        valid_local = anchor + timedelta(minutes=float(raw_step["offset_minutes"]))
        valid_utc = valid_local.astimezone(timezone.utc)
        family = raw_step["dominant_burnable_pressure_family"]
        base_asl = raw_step["dominant_burnable_cloud_base_asl_mean_m"]
        top_asl = raw_step["dominant_burnable_cloud_top_asl_mean_m"]
        terrain_asl = raw_step["dominant_burnable_target_terrain_asl_mean_m"]
        row = dict(raw_step)
        row.update({
            "valid_time_local": valid_local.isoformat(),
            "valid_time_utc": valid_utc.isoformat(),
            "dominant_burnable_cloud_base_agl_mean_m": (
                max(float(base_asl) - float(terrain_asl), 0.0)
                if family is not None else None
            ),
            "dominant_burnable_cloud_top_agl_mean_m": (
                max(float(top_asl) - float(terrain_asl), 0.0)
                if family is not None else None
            ),
            "dominant_burnable_cloud_base_above_observer_mean_m": (
                float(base_asl) - observer_elevation if family is not None else None
            ),
            "dominant_burnable_cloud_top_above_observer_mean_m": (
                float(top_asl) - observer_elevation if family is not None else None
            ),
            "dominant_burnable_cloud_phase": (
                {
                    "label": "unknown",
                    "ice_fraction": None,
                    "confidence": 0.0,
                    "source": "not-resolved-for-native-burnable-target",
                }
                if family is not None else None
            ),
        })
        steps.append(row)
        if current["best_offset_minutes"] is not None and math.isclose(
            float(raw_step["offset_minutes"]),
            float(current["best_offset_minutes"]),
            abs_tol=1e-9,
            rel_tol=0.0,
        ):
            best = row
    if current["best_offset_minutes"] is not None and best is None:
        raise ValueError("point core best step could not be materialized")

    base_asl = current["dominant_burnable_cloud_base_asl_mean_m"]
    top_asl = current["dominant_burnable_cloud_top_asl_mean_m"]
    terrain_asl = current["dominant_burnable_target_terrain_asl_mean_m"]
    family = current["dominant_burnable_pressure_family"]
    base_agl = (
        max(float(base_asl) - float(terrain_asl), 0.0)
        if family is not None else None
    )
    top_agl = (
        max(float(top_asl) - float(terrain_asl), 0.0)
        if family is not None else None
    )
    phase = (
        {
            "label": "unknown",
            "ice_fraction": None,
            "confidence": 0.0,
            "source": "not-resolved-for-native-burnable-target",
        }
        if family is not None else None
    )
    duration = int(round(float(current["burnable_duration_minutes"])))
    quality_index = float(current["quality_index"])
    summary = [
        "结果由目标坐标同次完整前方云场直接生成。",
        f"{'朝霞' if request.phenomenon == 'dawn' else '晚霞'}质量指数 "
        f"{quality_index:.3f}/2.5（{current['quality_level']}）。",
        f"可烧云占比峰值 {float(current['forward_burnable_cloud_field_amount_peak']) * 100:.1f}%，"
        f"入射 AOD 修正 {float(current['quality_components']['incoming_aod_adjustment']):+.3f}，"
        f"观测 AOD 修正 {float(current['quality_components']['observer_aod_adjustment']):+.3f}。",
    ]
    return {
        "observer_name": request.name,
        "phenomenon": request.phenomenon,
        "model_source": request.model_source,
        "forecast_mode": request.forecast_mode,
        "target_date": request.target_date.isoformat(),
        "analysis_radius_km": float(current["analysis_radius_km"]),
        "timezone_name": request.timezone_name,
        "observer_elevation_asl_m": observer_elevation,
        "anchor_local": anchor.isoformat(),
        "cycle_time_utc": current["cycle_time_utc"],
        "model_cycles": dict(current["model_cycles"]),
        "best_time_local": best["valid_time_local"] if best is not None else None,
        "overall_score": float(current["overall_score"]),
        "burn_score": float(current["peak_quality_index"]) / 2.5,
        "peak_quality_index": float(current["peak_quality_index"]),
        "peak_quality_level": current["peak_quality_level"],
        "intensity_score": float(best["intensity_score"]) if best is not None else 0.0,
        "extent_score": float(best["extent_score"]) if best is not None else 0.0,
        "vividness_score": float(best["vividness_score"]) if best is not None else 0.0,
        "quality_index": quality_index,
        "quality_level": current["quality_level"],
        "quality_components": dict(current["quality_components"]),
        "dominant_burnable_pressure_family": family,
        "dominant_burnable_cloud_base_asl_mean_m": base_asl,
        "dominant_burnable_cloud_top_asl_mean_m": top_asl,
        "dominant_burnable_target_terrain_asl_mean_m": terrain_asl,
        "dominant_burnable_cloud_base_agl_mean_m": (
            base_agl
        ),
        "dominant_burnable_cloud_top_agl_mean_m": (
            top_agl
        ),
        "dominant_burnable_cloud_base_above_observer_mean_m": (
            float(base_asl) - observer_elevation if family is not None else None
        ),
        "dominant_burnable_cloud_top_above_observer_mean_m": (
            float(top_asl) - observer_elevation if family is not None else None
        ),
        "dominant_burnable_cloud_phase": phase,
        "cloud_phase_proxy": (
            dict(best["cloud_phase_proxy"])
            if best is not None and best.get("cloud_phase_proxy") is not None
            else None
        ),
        "dominant_burnable_cloud_genus_diagnosis": cloud_genus_diagnosis(
            dominant_pressure_family=family,
            cloud_phase=phase,
            cloud_base_agl_m=base_agl,
            cloud_top_agl_m=top_agl,
            convective_risk=float(current["convective_risk"]),
            model_source=request.model_source,
        ),
        "cloud_genus_proxy": (
            dict(best["cloud_genus_proxy"])
            if best is not None and best.get("cloud_genus_proxy") is not None
            else None
        ),
        "confidence_score": float(current["confidence_score"]),
        "model_disagreement_score": float(current["model_disagreement_score"]),
        "duration_minutes": duration,
        "potential_duration_minutes": duration,
        "selected_regime": best["selected_regime"] if best is not None else "clear",
        "scene_scores": dict(best["scene_scores"]) if best is not None else {
            "clear": 1.0,
            "forward_cloud_field": 0.0,
            "burnable_cloud_field": 0.0,
            "solar_incoming_blocked_field": 0.0,
        },
        "local_sky_score": float(best["local_sky_score"]) if best is not None else 1.0,
        "remote_sky_score": float(best["remote_sky_score"]) if best is not None else 0.0,
        "convective_risk": float(current["convective_risk"]),
        "color_diagnosis": dict(best["color_diagnosis"]) if best is not None else {},
        "cloud_evolution": cloud_evolution_diagnosis(steps),
        "timing_window": score_curve_window(steps, regime="forward_cloud_field"),
        "calibrated_probability": None,
        "history_record_id": None,
        "model_weights": dict(current["model_weights"]),
        "source_roles": {"primary": "numerical_model", "auxiliary": "satellite_observation"},
        "aerosol": dict(current["aerosol"]),
        "summary": summary,
        "nowcast": None,
        "steps": steps,
        "html_report_path": None,
        "map_token": "",
        "map_times": [],
        "html_report_url": "",
        "point_refinement": current["point_refinement"],
    }
