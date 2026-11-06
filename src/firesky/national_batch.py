from __future__ import annotations

import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import math
import hashlib
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from numbers import Real
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Protocol, Sequence
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import numpy as np

from firesky.analysis_constants import DEFAULT_ANALYSIS_RADIUS_KM, MAX_ANALYSIS_RADIUS_KM
from firesky.aerosol import (
    CAMS_AEROSOL_PROFILE_COUNT,
    CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS,
    cams_profile_bracket_times,
    find_cached_cams_bracket,
    load_cams_optics_grid,
)
from firesky.forward_field_scoring import (
    ACTIVE_CLOUD_THRESHOLD,
    MIN_OBSERVER_VISIBILITY_FOR_SCORE,
    NONBURNABLE_BLOCKER_TOLERANCE,
    OBSERVER_CLOUD_FIELD_COMPONENT_FIELDS,
    OBSERVER_CLOUD_FIELD_SCORE_CEILING_COMPONENT_FIELDS,
    _active_time_mean,
    _event_active_cloud,
    score_instant_observer_cloud_field_batch,
    score_observer_cloud_field_batch,
    score_observer_cloud_field,
)
from firesky.geometry import (
    destination_point,
    required_illumination_height_m,
    spherical_horizon_margin_m,
    spherical_line_of_sight_height_m,
)
from firesky.forecast_characteristics import (
    cloud_evolution_diagnosis,
    cloud_genus_diagnosis,
    cloud_genus_proxy_diagnosis,
    cloud_phase_diagnosis,
    color_diagnosis,
    fire_cloud_quality_level,
    score_curve_window,
)
from firesky.gfs import (
    CLOUD_BOUNDARY_LEVEL_TYPES,
    GfsInputUnavailableError,
    GfsNomadsClient,
    PRESSURE_LEVELS,
    VARIABLES,
    _DecodedGfsGrid,
    bbox_contains,
    ensure_shared_decoded_grid_cache_capacity,
)
from firesky.version import NATIONAL_FORECAST_ALGORITHM_VERSION
from firesky.native_core import (
    NATIONAL_SAMPLE_FIELDS,
    national_assess_batch,
)
from firesky.scoring import (
    aerosol_lightpath_transmission_samples,
    aerosol_transmission_weight_from_samples,
    apparent_cloud_elevation_deg,
    curvature_drop_at_distance_m,
    distance_weight,
    high_cloud_suitability_score,
    horizon_proximity_weight,
    line_of_sight_weight,
    pressure_weight,
    sightline_height_at_distance_m,
    solar_ray_height_at_distance_m,
    vertical_ray_intersection_score,
    combine_regime_burn_score,
    combine_regime_intensity_score,
    solar_elevation_weight,
    visibility_gate,
)
from firesky.quality_core import duration_quality_factor
from firesky.models import AerosolReading, AerosolSample, GeoPoint
from firesky.bounded_lock import acquire_file_lock
from firesky.storage import derived_cache_dir
from firesky.time_contract import parse_utc_iso_datetime


NATIONAL_AEROSOL_PROFILE_MODE = (
    "cams-aerext532-11level-aod550-normalized-segment-v1"
)
NATIONAL_AEROSOL_EXTINCTION_UNITS = "m^-1"
NATIONAL_AEROSOL_TEMPORAL_INTERPOLATION = (
    "linear-adjacent-cams-3h-valid-time-v1"
)
NATIONAL_AEROSOL_SPATIAL_INTERPOLATION = (
    "bilinear-raw-aod550-aerext532-v1"
)
NATIONAL_AEROSOL_NORMALIZATION = (
    "aod550-after-raw-space-time-interpolation-v1"
)
NATIONAL_NEAR_LOW_RETURN_DIAGNOSTIC = "near-low-return-diagnostic-v1"
NATIONAL_QUALITY_INDEX_SEMANTICS = "empirical-potential-index-0-to-2.5-not-probability"
NATIONAL_CONFIDENCE_KIND = "input-and-geometry-support-not-event-hit-probability"
NATIONAL_FORECAST_LEAD_SEMANTICS = "valid-time-minus-model-cycle-hours"
NATIONAL_BATCH_ALGORITHM = f"national-batch-{NATIONAL_FORECAST_ALGORITHM_VERSION}-raytraced-sky-v61-isolated-column-recovery-window-supported-regional-coherent-sparse-upper-high-aod-separated-deep-target-self-transmission-opacity-only-bilinear-point-aod-service-unified-quality-isolated-aggregate-high-support-surface-gated-continuous-column-recovery-per-cloud-material-opacity-horizontal-lightpath-bilinear-v1-cross-section-aggregate-boundary-v1-terrain-profile-v1-adaptive-upper-middle-400-continuous-v2-observer-return-terrain-v1-spherical-observer-horizon-v1-cumulus-low-support-v1-isolated-aggregate-representative-300-v1-low-unsampled-layer-recovery-v1-grib-bitmap-v1-{NATIONAL_AEROSOL_PROFILE_MODE}-{NATIONAL_NEAR_LOW_RETURN_DIAGNOSTIC}"
NATIONAL_POINT_CORE_VERSION = 8
NATIONAL_POINT_CORE_ALGORITHM = (
    f"national-point-core-{NATIONAL_FORECAST_ALGORITHM_VERSION}-pre-veto-loss-ledger-v1-"
    f"{NATIONAL_AEROSOL_PROFILE_MODE}-{NATIONAL_NEAR_LOW_RETURN_DIAGNOSTIC}"
)
NATIONAL_DAWN_EVENT_OFFSETS_MINUTES = tuple(range(-75, 21, 5))
NATIONAL_DUSK_EVENT_OFFSETS_MINUTES = tuple(range(-20, 76, 5))
_AGGREGATE_BOUNDARY_EXTENSION_TOLERANCE_M = 1500.0
# Keep the optical-input provenance machine-readable.  The operational GFS
# product exposes CLWMR/ICMR but not effective-radius diagnostics, so the
# current material and path-opacity fields must not be consumed as absolute
# cloud optical depth or native GFS effective radius.
NATIONAL_CLOUD_OPTICS_METADATA = {
    "cloud_optics_source": "gfs-clwmr-icmr-material-proxy",
    "effective_radius_source": "unavailable-operational-gfs",
    "cloud_optical_depth_source": "native-path-opacity-proxy",
    "cloud_opacity_proxy_source": "rh-plus-clwmr-icmr-bounded-opacity-proxy",
    "cams_profile_normalization": "aod550-normalized-aerext532-segment-integration",
    "visual_response_mapping": "empirical-material-duration-response-not-radiance",
    "unavailable_spectral_quantities": "effective-radius-spectral-radiance-rgb-brightness-calibration",
}
NATIONAL_CLOUD_OPTICS_METADATA_BY_MODEL = {
    "gfs": dict(NATIONAL_CLOUD_OPTICS_METADATA),
    "ec": {
        "cloud_optics_source": "ec-public-tcc-rh-cloud-proxy",
        "effective_radius_source": "unavailable-ifs-public-basic-contract",
        "cloud_optical_depth_source": "native-path-opacity-proxy",
    },
}


def cloud_optics_metadata_for_model(model_source: str) -> dict[str, str]:
    """Return source-specific optics metadata without changing GFS defaults."""
    source = str(model_source).strip().lower()
    try:
        return dict(NATIONAL_CLOUD_OPTICS_METADATA_BY_MODEL[source])
    except KeyError as exc:
        raise ValueError(f"unsupported national model source: {source or '<empty>'}") from exc


def national_event_offsets_minutes(phenomenon: str) -> tuple[int, ...]:
    if phenomenon == "dawn":
        return NATIONAL_DAWN_EVENT_OFFSETS_MINUTES
    if phenomenon == "dusk":
        return NATIONAL_DUSK_EVENT_OFFSETS_MINUTES
    raise ValueError("phenomenon must be dawn or dusk")


class CanonicalRegionalGrid(Protocol):
    """Model-neutral regular grid consumed by the shared native scorer.

    Adapters for GFS, EC, and future models such as ICON normalize their raw
    fields into this vocabulary before column derivation. The scorer never
    reads a model-native object or model-specific field name.
    """

    latitudes: np.ndarray
    longitudes: np.ndarray
    fields: Mapping[tuple[str, int, str], np.ndarray]
NATIONAL_OBSERVER_FIELD_DIAGNOSTIC_COMPONENT_FIELDS = frozenset({
    *NATIONAL_CLOUD_OPTICS_METADATA,
    "solar_incoming_block_risk_at_best_cloud",
    "observer_return_optical_depth_mean",
    "observer_return_transmission_mean",
    "observer_local_return_optical_depth_mean",
    "observer_local_return_transmission_mean",
    "observer_return_model",
    "ground_area_view_amount_mean",
    "ground_area_burnable_amount_mean",
    "ground_area_burnable_load_mean",
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
NATIONAL_POINT_QUALITY_COMPONENT_FIELDS = (
    OBSERVER_CLOUD_FIELD_COMPONENT_FIELDS
    | NATIONAL_OBSERVER_FIELD_DIAGNOSTIC_COMPONENT_FIELDS
)
# These are selected-column diagnostics from the derived Python grid. They are
# deliberately named separately from native CloudLayerSample geometry, which
# remains the amount-weighted input used by the scorer.
NATIONAL_SELECTED_COLUMN_DIAGNOSTIC_SOURCE = "gfs-selected-column-bilinear-diagnostic"
EC_SELECTED_COLUMN_DIAGNOSTIC_SOURCE = "ec-public-tcc-rh-proxy-selected-column-bilinear-diagnostic"
# The observer scorer needs each layer's base/top/opacity and the model terrain
# height used by the incoming sunlight ray. Using sea level as the terrain
# surface lets rays pass through elevated ground and falsely illuminates
# near-surface mountain cloud.
GRID_FIELD_COUNT = 36
NATIONAL_GRID_HIGH_MATERIAL = 4
NATIONAL_GRID_MID_MATERIAL = 28
NATIONAL_GRID_LOW_MATERIAL = 29
# These three internal grid slots carry the resolved target-body risk for the
# high/middle/low family. Slot 3 remains a selected-column diagnostic only;
# native must never use that whole-column value as target self-transmission.
NATIONAL_GRID_HIGH_BODY_RISK = 9
NATIONAL_GRID_MID_BODY_RISK = 10
NATIONAL_GRID_LOW_BODY_RISK = 11
NATIONAL_GRID_HIGH_U = 30
NATIONAL_GRID_HIGH_V = 31
NATIONAL_GRID_MID_U = 32
NATIONAL_GRID_MID_V = 33
NATIONAL_GRID_LOW_U = 34
NATIONAL_GRID_LOW_V = 35
_CLOUD_FAMILY_CONNECTION_TOLERANCE_M = 900.0
_MIN_CONNECTED_TARGET_DECK_AMOUNT = 0.10
NATIONAL_GRID_AEROSOL_AOD550 = 25
NATIONAL_GRID_AEROSOL_RESERVED = 26
NATIONAL_GRID_LOCAL_UPPER_SUPPORT = 27

NATIONAL_SAMPLE_LOCAL_AOD = 5
NATIONAL_SAMPLE_VALID = 6
NATIONAL_SAMPLE_LEFT_SECONDS = 7
NATIONAL_SAMPLE_RIGHT_SECONDS = 8

_INFLOW_TARGET_BODY_KM = 10.0
_INFLOW_OPTICAL_LENGTH_KM = 140.0
_INFLOW_PEAK_WEIGHT = 1.0
_INFLOW_MIN_TRACE_KM = 200.0
_INFLOW_MAX_TRACE_KM = 1000.0
_INFLOW_CLEAR_HEIGHT_M = 20_000.0
NATIONAL_TARGET_LAYER_LIMIT = 4
# One canonical 1000 km observer/diagnostic domain is used by model scoring,
# atmospheric sections, and regional cache identities. Physical illumination
# and observer visibility are still resolved per cloud column; the radius is
# a data/diagnostic envelope, not a promise that every remote cloud is visible.
NATIONAL_ANALYSIS_RADIUS_KM = MAX_ANALYSIS_RADIUS_KM
NATIONAL_VIEW_DIAGNOSTIC_OFFSET = 43
NATIONAL_VIEW_DIAGNOSTIC_FIELDS = 34
# Diagnostic channels emitted by the authoritative observer-sky scorer.
NATIONAL_VIEW_AMOUNT_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET
NATIONAL_BURN_AMOUNT_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 1
NATIONAL_BURN_LOAD_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 2
NATIONAL_MATERIAL_LOAD_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 3
NATIONAL_DOMINANT_BURNABLE_PRESSURE_FAMILY_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 4
NATIONAL_HIGH_VIEW_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 5
NATIONAL_MID_VIEW_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 6
NATIONAL_LOW_VIEW_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 7
NATIONAL_HIGH_BURN_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 8
NATIONAL_MID_BURN_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 9
NATIONAL_LOW_BURN_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 10
NATIONAL_LOCAL_VIEW_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 11
NATIONAL_LOCAL_BURN_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 12
NATIONAL_LOCAL_LOAD_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 13
NATIONAL_GROUND_VIEW_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 14
NATIONAL_GROUND_BURN_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 15
NATIONAL_GROUND_LOAD_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 16
NATIONAL_INCOMING_BLOCK_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 17
NATIONAL_LOCAL_MATERIAL_LOAD_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 18
NATIONAL_INCOMING_AEROSOL_LOAD_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 19
NATIONAL_LOCAL_INCOMING_AEROSOL_LOAD_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 20
NATIONAL_TARGET_DECK_CONNECTED_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 21
NATIONAL_TARGET_DECK_ATTENUATED_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 22
NATIONAL_TARGET_DEEP_RISK_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 23
NATIONAL_CLOUD_OPTICAL_DEPTH_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 24
NATIONAL_PEAK_BLOCKING_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 25
NATIONAL_CLEAR_GAP_COUNT_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 26
NATIONAL_DOMINANT_BURNABLE_BASE_ASL_MEAN_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 27
NATIONAL_DOMINANT_BURNABLE_TOP_ASL_MEAN_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 28
NATIONAL_DOMINANT_BURNABLE_TARGET_TERRAIN_ASL_MEAN_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 29
NATIONAL_OBSERVER_RETURN_OPTICAL_DEPTH_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 30
NATIONAL_OBSERVER_RETURN_TRANSMISSION_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 31
NATIONAL_LOCAL_RETURN_OPTICAL_DEPTH_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 32
NATIONAL_LOCAL_RETURN_TRANSMISSION_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + 33
NATIONAL_VETO_LEDGER_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + NATIONAL_VIEW_DIAGNOSTIC_FIELDS
NATIONAL_VETO_CANDIDATE_OFFSET = NATIONAL_VETO_LEDGER_OFFSET
NATIONAL_VETO_TARGET_ELIGIBLE_OFFSET = NATIONAL_VETO_LEDGER_OFFSET + 1
NATIONAL_VETO_GEOMETRY_REACHABLE_OFFSET = NATIONAL_VETO_LEDGER_OFFSET + 2
NATIONAL_VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET = NATIONAL_VETO_LEDGER_OFFSET + 3
NATIONAL_VETO_UNOCCLUDED_BURN_UPPER_OFFSET = NATIONAL_VETO_LEDGER_OFFSET + 4
OUTPUT_FIELD_COUNT = NATIONAL_VETO_LEDGER_OFFSET + 5
# The frame now carries separate per-layer burn fields.  The former
# ``high_cloud``/``mid_cloud``/``low_cloud`` channels are view amounts and must
# never be interpreted as burnable amounts by Bot comparison diagnostics.
NATIONAL_FRAME_VERSION = 103
NATIONAL_PRECISE_POINT_SPACING_DEG = 0.25
_NATIONAL_FRAME_SELECTED_COLUMN_FIELDS = (0, 1, 2, 3, 7, 15, 16)

# The Python footprint helpers share the native observer-sky geometry used by
# the score and remain available to cross-section diagnostics. They describe
# the complete forward sky, not a camera focal length or a ranked target.
_VIEW_AZIMUTH_BINS = 36
_VIEW_AZIMUTH_HALF_DEG = 90.0
_VIEW_AZIMUTH_BIN_DEG = 5.0
_VIEW_ELEVATION_BINS = 60
# The primary cloud-area definition is the complete observer-centred
# sun-facing sector. It is intentionally independent of a camera focal length.
# A future camera diagnostic can use this same opacity field as a sidecar, but
# must not narrow the forecast score.
# Sidecar order retains every pressure-level field requested from GFS, plus a
# derived presence channel used by the vertical cloud/refinement pass.
LAYER_PROFILE_FIELDS = (
    "rh_percent",
    "geopotential_height_asl_m",
    "temperature_k",
    "omega_pa_s",
    "u_wind_ms",
    "v_wind_ms",
    "cloud_fraction_percent",
    "high_cloud_cover_percent",
    "high_cloud_deep_percent",
    "high_cloud_total_percent",
    "high_cloud_thin_percent",
    "cloud_liquid_kg_kg",
    "cloud_ice_kg_kg",
    "presence",
    "pressure_hpa",
    "middle_cloud_cover_percent",
    "low_cloud_cover_percent",
    "high_pressure_family_cloud_base_asl_m",
    "high_pressure_family_cloud_top_asl_m",
    "middle_pressure_family_cloud_base_asl_m",
    "middle_pressure_family_cloud_top_asl_m",
    "low_pressure_family_cloud_base_asl_m",
    "low_pressure_family_cloud_top_asl_m",
    "surface_elevation_asl_m",
)


def _local_cell_radius_km(cell_spacing_deg: float, latitude_deg: float) -> float:
    return float(
        0.5
        * abs(float(cell_spacing_deg))
        * math.sqrt(
            110.574
            * 111.320
            * max(0.2, abs(math.cos(math.radians(float(latitude_deg)))))
        )
    )


def _add_angular_cloud_footprint(
    opacity: np.ndarray,
    bearing_deg: float,
    solar_azimuth_deg: float,
    distance_km: float,
    cloud_base_m: float,
    cloud_top_m: float,
    cloud_fraction: float,
    cell_spacing_deg: float,
    latitude_deg: float,
) -> None:
    """Union one grid cell's cloud footprint into a fixed sunset sky view.

    ``opacity`` is an alpha union over azimuth/elevation bins.  Cloud amount
    controls horizontal occupancy, while the base/top determine its angular
    vertical extent.  This deliberately does not use vertical deck overlap.
    """
    if opacity.shape != (_VIEW_AZIMUTH_BINS, _VIEW_ELEVATION_BINS):
        raise ValueError("angular cloud opacity has an unexpected shape")
    distance = max(0.0, float(distance_km))
    base_m = max(0.0, float(cloud_base_m))
    top_m = max(base_m, float(cloud_top_m))
    fraction = float(np.clip(cloud_fraction, 0.0, 1.0))
    if fraction <= 0.0 or top_m <= 0.0:
        return
    center = (float(bearing_deg) - float(solar_azimuth_deg) + 180.0) % 360.0 - 180.0
    cell_width_km = abs(float(cell_spacing_deg)) * math.sqrt(
        110.574**2
        + (111.320 * math.cos(math.radians(float(latitude_deg)))) ** 2
    )
    local_radius_km = _local_cell_radius_km(cell_spacing_deg, latitude_deg)
    contains_observer = distance <= local_radius_km
    if contains_observer:
        azimuth_start, azimuth_end = (
            -_VIEW_AZIMUTH_HALF_DEG,
            _VIEW_AZIMUTH_HALF_DEG,
        )
    else:
        half_width = math.degrees(
            math.atan(0.5 * cell_width_km / max(distance, 1.0))
        )
        azimuth_start = max(-_VIEW_AZIMUTH_HALF_DEG, center - half_width)
        azimuth_end = min(_VIEW_AZIMUTH_HALF_DEG, center + half_width)
    if azimuth_end <= azimuth_start:
        return

    base_distance = max(local_radius_km, 1.0) if contains_observer else distance
    base_angle = float(np.clip(
        apparent_cloud_elevation_deg(base_distance, base_m),
        0.0,
        float(_VIEW_ELEVATION_BINS),
    ))
    top_angle = float(np.clip(
        apparent_cloud_elevation_deg(
            distance if not contains_observer else base_distance,
            top_m,
        ),
        0.0,
        float(_VIEW_ELEVATION_BINS),
    ))
    if top_angle <= base_angle or top_angle <= 0.0 or base_angle >= _VIEW_ELEVATION_BINS:
        return

    azimuth_bin_start = int(np.clip(
        math.floor((azimuth_start + _VIEW_AZIMUTH_HALF_DEG) / _VIEW_AZIMUTH_BIN_DEG),
        0,
        _VIEW_AZIMUTH_BINS,
    ))
    azimuth_bin_end = int(np.clip(
        math.ceil((azimuth_end + _VIEW_AZIMUTH_HALF_DEG) / _VIEW_AZIMUTH_BIN_DEG),
        0,
        _VIEW_AZIMUTH_BINS,
    ))
    elevation_start = int(np.clip(math.floor(base_angle), 0, _VIEW_ELEVATION_BINS))
    elevation_end = int(np.clip(math.ceil(top_angle), 0, _VIEW_ELEVATION_BINS))
    for azimuth_bin in range(azimuth_bin_start, azimuth_bin_end):
        bin_start = -_VIEW_AZIMUTH_HALF_DEG + azimuth_bin * _VIEW_AZIMUTH_BIN_DEG
        bin_end = bin_start + _VIEW_AZIMUTH_BIN_DEG
        azimuth_overlap = float(np.clip(
            (min(azimuth_end, bin_end) - max(azimuth_start, bin_start))
            / _VIEW_AZIMUTH_BIN_DEG,
            0.0,
            1.0,
        ))
        if azimuth_overlap <= 0.0:
            continue
        for elevation_bin in range(elevation_start, elevation_end):
            elevation_overlap = float(np.clip(
                min(top_angle, elevation_bin + 1.0)
                - max(base_angle, float(elevation_bin)),
                0.0,
                1.0,
            ))
            added = fraction * azimuth_overlap * elevation_overlap
            if added <= 0.0:
                continue
            current = float(np.clip(opacity[azimuth_bin, elevation_bin], 0.0, 1.0))
            opacity[azimuth_bin, elevation_bin] = 1.0 - (1.0 - current) * (1.0 - added)


def _angular_cloud_coverage(opacity: np.ndarray) -> float:
    """Return occupied fraction in the complete forward cloud field."""
    array = np.asarray(opacity, dtype=np.float64)
    if array.shape != (_VIEW_AZIMUTH_BINS, _VIEW_ELEVATION_BINS):
        raise ValueError("angular cloud opacity has an unexpected shape")
    elevation_edges = np.radians(np.arange(_VIEW_ELEVATION_BINS + 1, dtype=np.float64))
    solid_angle_weights = np.diff(np.sin(elevation_edges))
    occupied = float(np.sum(np.clip(array, 0.0, 1.0) * solid_angle_weights[None, :]))
    visible = _VIEW_AZIMUTH_BINS * math.sin(math.radians(_VIEW_ELEVATION_BINS))
    return float(np.clip(occupied / max(visible, 1e-9), 0.0, 1.0))


LAYER_PROFILE_FIELD_COUNT = len(LAYER_PROFILE_FIELDS)
_PROFILE_RH = 0
_PROFILE_HEIGHT = 1
_PROFILE_TEMPERATURE = 2
_PROFILE_OMEGA = 3
_PROFILE_U = 4
_PROFILE_V = 5
_PROFILE_FRACTION = 6
_PROFILE_HIGH_FRACTION = 7
_PROFILE_HIGH_DEEP = 8
_PROFILE_HIGH_TOTAL = 9
_PROFILE_HIGH_THIN = 10
_PROFILE_LIQUID = 11
_PROFILE_ICE = 12
_PROFILE_PRESENCE = 13
_PROFILE_PRESSURE = 14
_PROFILE_MIDDLE_FRACTION = 15
_PROFILE_LOW_FRACTION = 16
_PROFILE_HIGH_BASE = 17
_PROFILE_HIGH_TOP = 18
_PROFILE_MIDDLE_BASE = 19
_PROFILE_MIDDLE_TOP = 20
_PROFILE_LOW_BASE = 21
_PROFILE_LOW_TOP = 22
_PROFILE_SURFACE_ELEVATION = 23
_LEVEL_DEPTH_M = {
    200: 2200.0, 250: 2000.0, 300: 1800.0, 400: 1500.0,
    500: 1200.0, 600: 900.0, 700: 700.0, 750: 550.0,
    800: 500.0, 850: 450.0, 925: 300.0,
}
_PROFILE_DECK_MEMBER_AMOUNT = 0.35
_UNSAMPLED_LAYER_MIN_AGGREGATE_PERCENT = 5.0
_UNSAMPLED_LAYER_BOUNDARY_TOLERANCE_M = 150.0
# GFS pressure-level heights over China put 400 hPa near 7.5 km and 500 hPa
# near 5.8 km in summer.  Keep the layer split aligned with the tutorial's
# altitude rule (high cloud above roughly 6 km) instead of treating 500 hPa as
# high cloud and then classifying a thick middle deck as cirrus.
_HIGH_CLOUD_MAX_PRESSURE_HPA = 400.0
_MID_CLOUD_MAX_PRESSURE_HPA = 700.0
# A 400 hPa sample is near the high/middle boundary in summer. When a
# substantial 200/250/300 hPa deck is present, treat that sample as the upper
# part of the middle deck so the resolved high-cloud base remains tied to the
# actual upper deck. A saturated 400 hPa sample is instead retained as part of
# a continuous upper deck when the lower 500--700 hPa samples are weak, or
# both 250 and 300 hPa samples independently support the same upper deck. This
# prevents a single pressure sample from becoming a foreground middle-cloud
# wall while retaining the Hangzhou-style separated high/middle case.
_ADAPTIVE_MIDDLE_400_UPPER_SUPPORT_THRESHOLD = 0.30
_CONTINUOUS_UPPER_400_MIN_AMOUNT = 0.60
_CONTINUOUS_UPPER_400_MAX_LOWER_MIDDLE_AMOUNT = 0.40
_CONTINUOUS_UPPER_400_MIN_UPPER_PAIR_AMOUNT = 0.80
_ISOLATED_UPPER_HIGH_MAX_PRESSURE_HPA = 300.0
_ISOLATED_UPPER_HIGH_SATURATION_RH_PERCENT = 90.0
_ISOLATED_UPPER_HIGH_MAX_AMOUNT_FACTOR = 0.5
_ISOLATED_UPPER_HIGH_SATURATION_FACTOR_FLOOR = 0.10
_ISOLATED_UPPER_HIGH_SATURATION_FACTOR_WEIGHT = 0.40
_UPPER_HIGH_RELATIVE_SUPPORT_THRESHOLD = 0.20
_UPPER_HIGH_MIN_SUPPORT_FACTOR = 0.20
_UPPER_HIGH_SUPPORT_FACTOR_WEIGHT = 0.80
_TWO_LEVEL_UPPER_HIGH_MAX_PRESSURE_HPA = 400.0
# Some GFS cycles carry a broad HCC aggregate while pressure-level TCC is a
# sparse sample (for example, one 200 hPa cell and zero at 300 hPa).  Treat a
# verified sparse aggregate as one conservative representative deck rather
# than either discarding it or copying HCC into every pressure level.
_SPARSE_UPPER_HIGH_MIN_AGGREGATE_PERCENT = 70.0
_SPARSE_UPPER_HIGH_MAX_AGGREGATE_PERCENT = 80.0
_SPARSE_UPPER_HIGH_MAX_MEMBER_RATIO = 0.20
_SPARSE_UPPER_HIGH_MAX_MEMBER_PERCENT = 8.0
# Thin ice clouds can be represented by a low RH pressure sample; the
# explicit TCC/ice evidence still has to pass the other sparse-deck gates.
_SPARSE_UPPER_HIGH_MIN_MEMBER_RH_PERCENT = 55.0
_SPARSE_UPPER_HIGH_MIN_CONDENSATE_KG_KG = 3e-7
_SPARSE_UPPER_HIGH_SUPPORT_FACTOR = 0.20
_ISOLATED_AGGREGATE_HIGH_MIN_PERCENT = 55.0
# A saturated isolated slice is valid evidence even when the aggregate high
# deck is nearly complete. The aggregate is still admitted only once, at the
# representative 300 hPa level, so removing this arbitrary 90% ceiling does
# not copy a layer into every pressure sample or make a dense deck transparent.
_ISOLATED_AGGREGATE_HIGH_MAX_PERCENT = 100.0
_ISOLATED_AGGREGATE_HIGH_MIN_MEMBER_PERCENT = 15.0
_ISOLATED_AGGREGATE_HIGH_MAX_MEMBER_RATIO = 0.80
_ISOLATED_AGGREGATE_HIGH_MIN_RH_PERCENT = 95.0
# GFS condensate fields are quantized; keep a small but real ice/liquid signal
# (rather than RH alone) while avoiding a hard edge that rejects a saturated
# 300 hPa deck at 2.8e-6 kg/kg.
_ISOLATED_AGGREGATE_HIGH_MIN_CONDENSATE_KG_KG = 2.5e-6


def _piecewise_linear(value: float, points: Sequence[tuple[float, float]]) -> float:
    if value <= points[0][0]:
        return float(points[0][1])
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if value <= right_x:
            ratio = (value - left_x) / max(right_x - left_x, 1e-9)
            return float(left_y + np.clip(ratio, 0.0, 1.0) * (right_y - left_y))
    return float(points[-1][1])


@dataclass(frozen=True)
class NationalBatchPrepared:
    grid: np.ndarray
    hours: np.ndarray
    rows: int
    cols: int
    lat0: float
    lon0: float
    spacing: float
    latitudes: np.ndarray
    longitudes: np.ndarray
    cache_paths: list[str]
    layer_profiles: np.ndarray | None = None
    pressure_levels: tuple[int, ...] = ()
    layer_profile_path: str | None = None
    model_source: str = "gfs"
    model_contract: str | None = None
    surface_elevation_m: np.ndarray | None = None
    boundary_temporal_semantics: str = "unknown"


@dataclass(frozen=True)
class _HorizontalStencil:
    cells: np.ndarray
    weights: np.ndarray
    one_hot_mask: np.ndarray
    one_hot_cells: np.ndarray


def _horizontal_stencil_for_coordinates(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    *,
    lat0: float,
    lon0: float,
    spacing: float,
    rows: int,
    cols: int,
) -> _HorizontalStencil:
    """Build the same clamped four-corner stencil as native horizontal_weights."""
    if rows <= 0 or cols <= 0 or not math.isfinite(float(spacing)) or spacing <= 0.0:
        raise ValueError("horizontal grid spacing and dimensions must be positive")
    latitude_values = np.asarray(latitudes, dtype=np.float32)
    longitude_values = np.asarray(longitudes, dtype=np.float32)
    if latitude_values.shape != longitude_values.shape:
        raise ValueError("horizontal latitude/longitude coordinates must have the same shape")
    row_position = (latitude_values - np.float32(lat0)) / np.float32(spacing)
    col_position = (longitude_values - np.float32(lon0)) / np.float32(spacing)
    row0 = np.floor(row_position).clip(0, rows - 1).astype(np.int64)
    col0 = np.floor(col_position).clip(0, cols - 1).astype(np.int64)
    row1 = np.minimum(row0 + 1, rows - 1)
    col1 = np.minimum(col0 + 1, cols - 1)
    row_weight = np.clip(
        row_position - row0.astype(np.float32), 0.0, 1.0
    ).astype(np.float32, copy=False)
    col_weight = np.clip(
        col_position - col0.astype(np.float32), 0.0, 1.0
    ).astype(np.float32, copy=False)
    cells = np.stack(
        (
            row0 * cols + col0,
            row0 * cols + col1,
            row1 * cols + col0,
            row1 * cols + col1,
        ),
        axis=1,
    )
    weights = np.stack(
        (
            (1.0 - row_weight) * (1.0 - col_weight),
            (1.0 - row_weight) * col_weight,
            row_weight * (1.0 - col_weight),
            row_weight * col_weight,
        ),
        axis=1,
    ).astype(np.float32, copy=False)
    one_hot_mask = np.sum(weights == 1.0, axis=1) == 1
    one_hot_mask &= np.all((weights == 0.0) | (weights == 1.0), axis=1)
    one_hot_cells = np.take_along_axis(cells, np.argmax(weights, axis=1)[:, None], axis=1)[:, 0]
    return _HorizontalStencil(
        cells=cells,
        weights=weights,
        one_hot_mask=one_hot_mask,
        one_hot_cells=one_hot_cells,
    )


def _horizontal_stencil(
    prepared: NationalBatchPrepared,
    points: Sequence[dict[str, Any]],
) -> _HorizontalStencil:
    return _horizontal_stencil_for_coordinates(
        np.asarray([float(point["lat"]) for point in points], dtype=np.float32),
        np.asarray([float(point["lon"]) for point in points], dtype=np.float32),
        lat0=prepared.lat0,
        lon0=prepared.lon0,
        spacing=prepared.spacing,
        rows=prepared.rows,
        cols=prepared.cols,
    )


def _horizontal_sample_tensor_step(
    tensor: np.ndarray,
    sample: np.ndarray,
    stencil: _HorizontalStencil,
    *,
    chunk_points: int = 4096,
) -> np.ndarray:
    """Sample one temporal slice with native temporal-then-horizontal semantics.

    Each corner is first time-interpolated with a finite-value fallback. The
    remaining finite corners then receive a normalized share of their native
    horizontal weight. A field with no finite corner follows native's zero
    result; it never switches to a nearest-cell value.
    """
    values = np.asarray(tensor)
    samples = np.asarray(sample)
    if values.ndim < 3 or samples.ndim != 2 or samples.shape[1] != NATIONAL_SAMPLE_FIELDS:
        raise ValueError(
            "horizontal sampling requires tensor(time, cell, ...) and "
            f"sample(point, {NATIONAL_SAMPLE_FIELDS} fields)"
        )
    point_count = samples.shape[0]
    if (
        stencil.cells.shape != (point_count, 4)
        or stencil.weights.shape != (point_count, 4)
        or stencil.one_hot_mask.shape != (point_count,)
        or stencil.one_hot_cells.shape != (point_count,)
    ):
        raise ValueError("horizontal stencil must contain four corners per sample point")
    output = np.empty((point_count, *values.shape[2:]), dtype=np.float32)
    left = np.clip(samples[:, 2].astype(np.int64), 0, values.shape[0] - 1)
    right = np.clip(samples[:, 3].astype(np.int64), 0, values.shape[0] - 1)
    time_weight = np.clip(samples[:, 4], 0.0, 1.0).astype(np.float32, copy=False)
    chunk_size = max(1, int(chunk_points))
    for start in range(0, point_count, chunk_size):
        stop = min(point_count, start + chunk_size)
        one_hot = stencil.one_hot_mask[start:stop]
        if np.any(one_hot):
            one_hot_indices = np.flatnonzero(one_hot)
            one_hot_cells = stencil.one_hot_cells[start:stop][one_hot]
            first = np.asarray(
                values[left[start:stop][one_hot_indices], one_hot_cells],
                dtype=np.float32,
            )
            second = np.asarray(
                values[right[start:stop][one_hot_indices], one_hot_cells],
                dtype=np.float32,
            )
            one_hot_weight = time_weight[start:stop][one_hot_indices].reshape(
                (one_hot_indices.size,) + (1,) * (values.ndim - 2)
            )
            with np.errstate(invalid="ignore"):
                blended = first + (second - first) * one_hot_weight
            finite_first = np.isfinite(first)
            finite_second = np.isfinite(second)
            values_at_cell = np.where(
                finite_first & finite_second,
                blended,
                np.where(finite_first, first, second),
            )
            finite = np.isfinite(values_at_cell)
            output[start + one_hot_indices] = np.where(
                finite,
                values_at_cell,
                0.0,
            )

        off_grid = ~one_hot
        if np.any(off_grid):
            off_grid_indices = np.flatnonzero(off_grid)
            corner_cells = stencil.cells[start:stop][off_grid]
            first = np.asarray(
                values[left[start:stop][off_grid_indices, None], corner_cells],
                dtype=np.float32,
            )
            second = np.asarray(
                values[right[start:stop][off_grid_indices, None], corner_cells],
                dtype=np.float32,
            )
            off_grid_weight = time_weight[start:stop][off_grid_indices].reshape(
                (off_grid_indices.size, 1) + (1,) * (values.ndim - 2)
            )
            with np.errstate(invalid="ignore"):
                blended = first + (second - first) * off_grid_weight
            finite_first = np.isfinite(first)
            finite_second = np.isfinite(second)
            corner_values = np.where(
                finite_first & finite_second,
                blended,
                np.where(finite_first, first, second),
            )
            corner_weights = stencil.weights[start:stop][off_grid]
            for _ in range(corner_values.ndim - 2):
                corner_weights = corner_weights[..., None]
            finite = np.isfinite(corner_values)
            weighted_values = np.where(finite, corner_values, 0.0) * corner_weights
            valid_weights = np.where(finite, corner_weights, 0.0)
            numerator = np.sum(weighted_values, axis=1, dtype=np.float32)
            denominator = np.sum(valid_weights, axis=1, dtype=np.float32)
            output[start + off_grid_indices] = np.divide(
                numerator,
                denominator,
                out=np.zeros_like(numerator, dtype=np.float32),
                where=denominator > 0.0,
            )
    return output


def _horizontal_sample_fields(
    prepared: NationalBatchPrepared,
    samples: np.ndarray,
    stencil: _HorizontalStencil,
    fields: Sequence[int],
    *,
    chunk_points: int = 4096,
) -> np.ndarray:
    """Sample selected-column grid diagnostics using one shared stencil."""
    field_indices = np.asarray(tuple(fields), dtype=np.intp)
    if field_indices.ndim != 1 or field_indices.size == 0:
        raise ValueError("fields must contain at least one grid field")
    if np.any(field_indices < 0) or np.any(field_indices >= prepared.grid.shape[2]):
        raise ValueError("grid field index is outside the prepared tensor")
    samples = np.asarray(samples)
    if samples.ndim != 3 or samples.shape[2] != NATIONAL_SAMPLE_FIELDS:
        raise ValueError(
            f"horizontal sample tensor must have {NATIONAL_SAMPLE_FIELDS} fields"
        )
    point_count, step_count = samples.shape[:2]
    if stencil.cells.shape != (point_count, 4):
        raise ValueError("horizontal stencil must contain one four-corner row per point")
    output = np.empty(
        (point_count, step_count, field_indices.size),
        dtype=np.float32,
    )
    grid_fields = prepared.grid[:, :, field_indices]
    for step_index in range(step_count):
        output[:, step_index] = _horizontal_sample_tensor_step(
            grid_fields,
            samples[:, step_index],
            stencil,
            chunk_points=chunk_points,
        )
    return output


@dataclass(frozen=True)
class _RegularGridSampler:
    latitudes: np.ndarray
    longitudes: np.ndarray
    matrix: np.ndarray


@dataclass(frozen=True)
class _RegularGridStencil:
    lat0: np.ndarray
    lat1: np.ndarray
    lat_weight: np.ndarray
    lon0: np.ndarray
    lon1: np.ndarray
    lon_weight: np.ndarray
    nearest_lat: np.ndarray
    nearest_lon: np.ndarray
    inside: np.ndarray


def _regular_grid_axes(
    grid_lats: np.ndarray,
    grid_lons: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    grid_lats = np.asarray(grid_lats, dtype=np.float64)
    normalized_lons = (np.asarray(grid_lons, dtype=np.float64) + 180.0) % 360.0 - 180.0
    unique_lats = np.unique(grid_lats)
    unique_lons = np.unique(normalized_lons)
    return (
        unique_lats,
        unique_lons,
        np.searchsorted(unique_lats, grid_lats),
        np.searchsorted(unique_lons, normalized_lons),
    )


@dataclass(frozen=True)
class _GridDerivationInputs:
    """Shared pressure-column inputs for the column and profile outputs."""

    levels: tuple[int, ...]
    surface_elevation: np.ndarray
    high_layer_fraction: np.ndarray
    middle_layer_fraction: np.ndarray
    low_layer_fraction: np.ndarray
    high_fraction_semantics: str
    middle_fraction_semantics: str
    low_fraction_semantics: str
    instant_layer_fractions: tuple[np.ndarray, np.ndarray, np.ndarray]
    raw_fractions: np.ndarray
    raw_rhs: np.ndarray
    raw_liquid: np.ndarray
    raw_ice: np.ndarray
    heights: np.ndarray
    temperatures: np.ndarray
    omega: np.ndarray
    u_winds: np.ndarray
    v_winds: np.ndarray
    high_deep: np.ndarray
    high_total: np.ndarray
    high_thin: np.ndarray
    boundary_heights: dict[str, tuple[np.ndarray, np.ndarray]]
    resolved_fractions: np.ndarray
    raw_sparse_high_support: np.ndarray
    sparse_high_support: np.ndarray


_GRID_DERIVATION_CONTEXT = threading.local()


def _current_grid_derivation_inputs(
    grid: CanonicalRegionalGrid,
) -> _GridDerivationInputs | None:
    context = getattr(_GRID_DERIVATION_CONTEXT, "values", None)
    if not context:
        return None
    entry = context.get(id(grid))
    if entry is None or entry[0] is not grid:
        return None
    return entry[1]


def _prepare_grid_derivation(grid: CanonicalRegionalGrid) -> _GridDerivationInputs:
    """Read and resolve pressure fields once for both native sidecars."""
    _validate_canonical_grid_field_ranges(grid)
    levels = tuple(
        level
        for level in PRESSURE_LEVELS
        if ("isobaricInhPa", level, "gh") in grid.fields
        and np.any(np.isfinite(_field(grid, "isobaricInhPa", level, "gh")))
    )
    if not levels:
        raise RuntimeError("GFS decoded grid has no supported pressure levels")

    surface_elevation = np.nan_to_num(
        _field(grid, "surface", 0, "orog"), nan=0.0
    )
    high_layer_fraction, high_fraction_semantics = _layer_fraction_with_provenance(
        grid, "highCloudLayer", "hcc", "avg_hcc"
    )
    middle_layer_fraction, middle_fraction_semantics = _layer_fraction_with_provenance(
        grid, "middleCloudLayer", "mcc", "avg_mcc"
    )
    low_layer_fraction, low_fraction_semantics = _layer_fraction_with_provenance(
        grid, "lowCloudLayer", "lcc", "avg_lcc"
    )
    instant_layer_fractions = tuple(
        np.where(semantics == "instantaneous", fraction, np.nan)
        for fraction, semantics in (
            (high_layer_fraction, high_fraction_semantics),
            (middle_layer_fraction, middle_fraction_semantics),
            (low_layer_fraction, low_fraction_semantics),
        )
    )
    raw_fractions = np.stack([
        _field_alias(grid, "isobaricInhPa", level, "tcc", "tcdc")
        for level in levels
    ])
    raw_rhs = np.stack([
        _field(grid, "isobaricInhPa", level, "r")
        for level in levels
    ])
    raw_liquid = np.stack([
        _field(grid, "isobaricInhPa", level, "clwmr")
        for level in levels
    ])
    raw_ice = np.stack([
        _field(grid, "isobaricInhPa", level, "icmr")
        for level in levels
    ])
    heights = np.stack([
        _field(grid, "isobaricInhPa", level, "gh")
        for level in levels
    ])
    temperatures = np.stack([
        _field_alias(grid, "isobaricInhPa", level, "t", "tmp")
        for level in levels
    ])
    omega = np.stack([
        _field_alias(grid, "isobaricInhPa", level, "w", "vvel")
        for level in levels
    ])
    u_winds = np.stack([
        _field_alias(grid, "isobaricInhPa", level, "u", "ugrd")
        for level in levels
    ])
    v_winds = np.stack([
        _field_alias(grid, "isobaricInhPa", level, "v", "vgrd")
        for level in levels
    ])
    high_deep = _field(grid, "highCloudLayer", 0, "hcdc")
    high_total = _field(grid, "highCloudLayer", 0, "tcc")
    high_thin = _field(grid, "highCloudLayer", 0, "tcdc")
    raw_sparse_high_support = _sparse_upper_high_support(
        levels,
        raw_fractions,
        raw_rhs,
        instant_layer_fractions[0],
        raw_liquid,
        raw_ice,
    )
    sparse_high_support = _coherent_sparse_upper_high_support(
        raw_sparse_high_support,
        grid.latitudes,
        grid.longitudes,
    )
    boundary_heights = {
        family: _cloud_layer_boundary_heights(
            grid,
            family,
            levels,
            heights,
            temperatures,
            surface_elevation,
        )
        for family in _CLOUD_BOUNDARY_FAMILIES
    }
    resolved_fractions = _resolve_unsampled_layer_fractions(
        levels,
        raw_fractions,
        instant_layer_fractions,
        heights,
        surface_elevation,
        boundary_heights,
        (high_fraction_semantics, middle_fraction_semantics, low_fraction_semantics),
    )
    return _GridDerivationInputs(
        levels=levels,
        surface_elevation=surface_elevation,
        high_layer_fraction=high_layer_fraction,
        middle_layer_fraction=middle_layer_fraction,
        low_layer_fraction=low_layer_fraction,
        high_fraction_semantics=high_fraction_semantics,
        middle_fraction_semantics=middle_fraction_semantics,
        low_fraction_semantics=low_fraction_semantics,
        instant_layer_fractions=instant_layer_fractions,
        raw_fractions=raw_fractions,
        raw_rhs=raw_rhs,
        raw_liquid=raw_liquid,
        raw_ice=raw_ice,
        heights=heights,
        temperatures=temperatures,
        omega=omega,
        u_winds=u_winds,
        v_winds=v_winds,
        high_deep=high_deep,
        high_total=high_total,
        high_thin=high_thin,
        boundary_heights=boundary_heights,
        resolved_fractions=resolved_fractions,
        raw_sparse_high_support=raw_sparse_high_support,
        sparse_high_support=sparse_high_support,
    )


def _field(grid: CanonicalRegionalGrid, level_type: str, level: int, name: str) -> np.ndarray:
    value = grid.fields.get((level_type, level, name))
    if value is None:
        return np.full(grid.latitudes.shape, np.nan, dtype=np.float32)
    return np.asarray(value, dtype=np.float32)


def _field_alias(grid: CanonicalRegionalGrid, level_type: str, level: int, *names: str) -> np.ndarray:
    result = np.full(grid.latitudes.shape, np.nan, dtype=np.float32)
    for name in names:
        if (level_type, level, name) in grid.fields:
            candidate = _field(grid, level_type, level, name)
            if candidate.shape != result.shape:
                return candidate
            result = np.where(np.isfinite(result), result, candidate)
    return result


def _layer_fraction_with_provenance(
    grid: _DecodedGfsGrid, layer_type: str, *names: str
) -> tuple[np.ndarray, str]:
    """Read an aggregate fraction and retain instantaneous/average provenance."""
    for name in names:
        if (layer_type, 0, name) in grid.fields:
            source = "average" if name.startswith("avg_") else "instantaneous"
            return _field(grid, layer_type, 0, name), source
    return np.full(grid.latitudes.shape, np.nan, dtype=np.float32), "missing"


def _layer_fraction(grid: _DecodedGfsGrid, layer_type: str, *names: str) -> np.ndarray:
    """Compatibility wrapper returning the aggregate value."""
    return _layer_fraction_with_provenance(grid, layer_type, *names)[0]


_CLOUD_BOUNDARY_PRESSURE_PA_MISSING = 9999.0
_CLOUD_BOUNDARY_FAMILIES = ("high", "middle", "low")


def _cloud_boundary_pressure(
    grid: _DecodedGfsGrid,
    family: str,
    boundary: str,
) -> np.ndarray:
    """Read a GFS aggregate cloud-layer pressure boundary in pascals.

    NOMADS exposes these fields as ``avg_pres`` at the special
    ``*CloudBottom``/``*CloudTop`` levels.  Some products use the instantaneous
    ``pres`` short name instead, so both names are accepted.  The caller masks
    the GRIB missing sentinel (9999 Pa) before converting to height.
    """
    if family not in _CLOUD_BOUNDARY_FAMILIES or boundary not in {"bottom", "top"}:
        raise ValueError(f"unsupported cloud boundary: {family}/{boundary}")
    level_type = f"{family}Cloud{boundary.title()}"
    if level_type not in CLOUD_BOUNDARY_LEVEL_TYPES:
        raise ValueError(f"unsupported GFS cloud boundary level type: {level_type}")
    # ``pres`` and ``avg_pres`` carry different temporal semantics. Do not
    # merge them per cell: a bitmap gap in an instantaneous field must remain
    # missing instead of being filled from an averaged product.
    if (level_type, 0, "pres") in grid.fields:
        return _field(grid, level_type, 0, "pres")
    return _field(grid, level_type, 0, "avg_pres")


def _pressure_boundary_height_m(
    pressure_pa: np.ndarray,
    pressure_levels_hpa: np.ndarray,
    geopotential_heights_m: np.ndarray,
    temperatures_k: np.ndarray,
) -> np.ndarray:
    """Convert a GFS cloud-boundary pressure to geometric height.

    Boundary products are pressure surfaces, while the native ray tracer uses
    metres.  Interpolate the model geopotential profile linearly in log
    pressure (the hypsometric coordinate), with a local scale-height
    extrapolation only outside the requested pressure levels.  Missing or
    incomplete profiles stay NaN and therefore use the existing pressure-level
    geometry fallback.
    """
    target = np.asarray(pressure_pa, dtype=np.float64) / 100.0
    levels = np.asarray(pressure_levels_hpa, dtype=np.float64)
    heights = np.asarray(geopotential_heights_m, dtype=np.float64)
    temperatures = np.asarray(temperatures_k, dtype=np.float64)
    if (
        target.ndim != 1
        or levels.ndim != 1
        or heights.shape != (levels.size, target.size)
        or temperatures.shape != heights.shape
        or levels.size < 2
    ):
        return np.full(target.shape, np.nan, dtype=np.float32)
    complete_profile = np.all(np.isfinite(heights), axis=0)
    valid = (
        np.isfinite(target)
        & (target > 0.0)
        & ~np.isclose(
            np.asarray(pressure_pa, dtype=np.float64),
            _CLOUD_BOUNDARY_PRESSURE_PA_MISSING,
            atol=0.5,
        )
        & complete_profile
    )
    output = np.full(target.shape, np.nan, dtype=np.float64)
    if not np.any(valid):
        return output.astype(np.float32)

    log_levels = np.log(levels)
    log_target = np.log(np.clip(target, levels[0] * 0.02, levels[-1] * 2.0))
    columns = np.arange(target.size)
    upper = np.searchsorted(log_levels, log_target, side="right")
    upper = np.clip(upper, 1, levels.size - 1)
    lower = upper - 1
    h_lower = heights[lower, columns]
    h_upper = heights[upper, columns]
    ratio = (log_target - log_levels[lower]) / (
        log_levels[upper] - log_levels[lower]
    )
    interpolated = h_lower + ratio * (h_upper - h_lower)

    gas_constant = 287.05
    gravity = 9.80665
    temperature_lower = temperatures[lower, columns]
    temperature_upper = temperatures[upper, columns]
    fallback_temperature = 250.0
    temperature_lower = np.where(
        np.isfinite(temperature_lower), temperature_lower, fallback_temperature
    )
    temperature_upper = np.where(
        np.isfinite(temperature_upper), temperature_upper, fallback_temperature
    )
    lower_extrapolation = target < levels[0]
    upper_extrapolation = target > levels[-1]
    low_temperature = temperature_lower
    high_temperature = temperature_upper
    interpolated = np.where(
        lower_extrapolation,
        heights[0, columns]
        + gas_constant
        * low_temperature
        / gravity
        * np.log(levels[0] / np.maximum(target, 1e-6)),
        interpolated,
    )
    interpolated = np.where(
        upper_extrapolation,
        heights[-1, columns]
        - gas_constant
        * high_temperature
        / gravity
        * np.log(np.maximum(target, 1e-6) / levels[-1]),
        interpolated,
    )
    output[valid] = interpolated[valid]
    return output.astype(np.float32)


def _cloud_layer_boundary_heights(
    grid: _DecodedGfsGrid,
    family: str,
    pressure_levels: Sequence[int],
    heights_m: np.ndarray,
    temperatures_k: np.ndarray,
    surface_elevation_m: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return model-provided aggregate cloud base/top heights when present."""
    bottom_pressure = _cloud_boundary_pressure(grid, family, "bottom")
    top_pressure = _cloud_boundary_pressure(grid, family, "top")
    levels = np.asarray(pressure_levels, dtype=np.float32)
    base = _pressure_boundary_height_m(
        bottom_pressure,
        levels,
        heights_m,
        temperatures_k,
    )
    top = _pressure_boundary_height_m(
        top_pressure,
        levels,
        heights_m,
        temperatures_k,
    )
    valid = np.isfinite(base) & np.isfinite(top) & (top > base)
    base = np.where(valid, np.maximum(base, surface_elevation_m), np.nan)
    top = np.where(valid, np.maximum(top, base), np.nan)
    return base.astype(np.float32), top.astype(np.float32)


def _cloud_boundary_temporal_semantics(grid: _DecodedGfsGrid) -> str:
    """Describe whether both aggregate cloud boundaries are instantaneous."""
    semantics: list[str] = []
    for family in _CLOUD_BOUNDARY_FAMILIES:
        for boundary in ("bottom", "top"):
            level_type = f"{family}Cloud{boundary.title()}"
            if (level_type, 0, "pres") in grid.fields:
                semantics.append("instantaneous")
            elif (level_type, 0, "avg_pres") in grid.fields:
                semantics.append("average")
            else:
                semantics.append("missing")
    if "average" in semantics:
        return "average-envelope-not-instantaneous"
    if semantics and all(value == "instantaneous" for value in semantics):
        return "instantaneous"
    return "unknown"


_GFS_PERCENT_FIELDS = frozenset(
    {
        "tcc", "tcdc", "hcc", "avg_hcc", "hcdc", "mcc", "avg_mcc",
        "mcdc", "lcc", "avg_lcc", "lcdc", "r",
    }
)


def _validate_canonical_grid_field_ranges(grid: CanonicalRegionalGrid) -> None:
    """Reject finite decoded values outside their GRIB field semantics."""
    expected_shape = np.asarray(grid.latitudes).shape
    for (level_type, level, name), raw_values in grid.fields.items():
        values = np.asarray(raw_values, dtype=np.float32)
        if values.shape != expected_shape or values.ndim != 1:
            raise RuntimeError(
                f"GFS field {level_type}/{level}/{name} has an invalid grid shape"
            )
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            continue
        lower: float | None = None
        upper: float | None = None
        if name in _GFS_PERCENT_FIELDS:
            lower, upper = 0.0, 100.0
        elif name in {"clwmr", "icmr"}:
            lower, upper = 0.0, 1.0
        elif name == "gh":
            lower, upper = -1_000.0, 50_000.0
        elif name == "orog":
            lower, upper = -500.0, 9_000.0
        elif name in {"pres", "avg_pres"}:
            # 9999 Pa is the product's field-specific missing sentinel. It is
            # retained here and masked by the boundary converter, never used as
            # a global missing-value rule for unrelated fields.
            valid = finite[(finite != _CLOUD_BOUNDARY_PRESSURE_PA_MISSING)]
            if valid.size:
                lower, upper = 100.0, 110_000.0
                finite = valid
        if lower is not None and (
            np.any(finite < lower) or np.any(finite > upper)
        ):
            raise RuntimeError(
                f"GFS field {level_type}/{level}/{name} contains values outside "
                f"[{lower}, {upper}]"
            )


def _resolve_unsampled_layer_fractions(
    levels: Sequence[int],
    fractions: np.ndarray,
    layer_fractions: tuple[np.ndarray, np.ndarray, np.ndarray],
    heights_m: np.ndarray,
    surface_elevation_m: np.ndarray,
    boundary_heights: dict[str, tuple[np.ndarray, np.ndarray]],
    aggregate_temporal_semantics: tuple[str, str, str] = (
        "instantaneous", "instantaneous", "instantaneous"
    ),
) -> np.ndarray:
    """Recover one bounded representative for a layer between pressure slices.

    Aggregate layer cover is a horizontal union, not a value to paste into all
    pressure levels. A representative is admitted only when the aggregate is
    instantaneous, both model boundaries are usable, no pressure sample lies
    inside the boundary envelope, and the envelope is bracketed by sampled
    above-ground levels. Existing pressure members and explicit clear slices
    therefore remain authoritative.
    """
    resolved = np.array(fractions, dtype=np.float32, copy=True)
    level_values = np.asarray(levels, dtype=np.float32)
    heights = np.asarray(heights_m, dtype=np.float32)
    surface = np.asarray(surface_elevation_m, dtype=np.float32)
    if resolved.ndim != 2 or heights.shape != resolved.shape:
        return resolved
    family_specs = (
        ("high", 0, _HIGH_CLOUD_MAX_PRESSURE_HPA, None),
        ("middle", 1, _MID_CLOUD_MAX_PRESSURE_HPA, _HIGH_CLOUD_MAX_PRESSURE_HPA),
        ("low", 2, np.inf, _MID_CLOUD_MAX_PRESSURE_HPA),
    )
    columns = np.arange(resolved.shape[1])
    above_ground = np.isfinite(heights) & (heights > surface[None, :] + 25.0)
    for family, family_index, upper_pressure, lower_pressure in family_specs:
        if lower_pressure is None:
            family_mask = level_values <= upper_pressure
        elif np.isinf(upper_pressure):
            family_mask = level_values > lower_pressure
        else:
            family_mask = (level_values > lower_pressure) & (level_values <= upper_pressure)
        family_indices = np.flatnonzero(family_mask)
        if family_indices.size == 0:
            continue
        aggregate = np.asarray(layer_fractions[family_index], dtype=np.float32)
        direct_base, direct_top = boundary_heights[family]
        valid_boundary = (
            (aggregate_temporal_semantics[family_index] == "instantaneous")
            & np.isfinite(aggregate)
            & (aggregate >= _UNSAMPLED_LAYER_MIN_AGGREGATE_PERCENT)
            & np.isfinite(direct_base)
            & np.isfinite(direct_top)
            & (direct_top > direct_base)
        )
        if not np.any(valid_boundary):
            continue
        envelope_inside = (
            above_ground
            & (heights >= direct_base[None, :] - _UNSAMPLED_LAYER_BOUNDARY_TOLERANCE_M)
            & (heights <= direct_top[None, :] + _UNSAMPLED_LAYER_BOUNDARY_TOLERANCE_M)
        )
        sampled_inside = np.any(envelope_inside[family_indices], axis=0)
        has_existing_member = np.any(
            envelope_inside[family_indices]
            & np.isfinite(resolved[family_indices])
            & (resolved[family_indices] >= ACTIVE_CLOUD_THRESHOLD * 100.0),
            axis=0,
        )
        lower_bracket = np.any(
            above_ground[family_indices]
            & (heights[family_indices] <= direct_base[None, :] + _UNSAMPLED_LAYER_BOUNDARY_TOLERANCE_M),
            axis=0,
        )
        upper_bracket = np.any(
            above_ground[family_indices]
            & (heights[family_indices] >= direct_top[None, :] - _UNSAMPLED_LAYER_BOUNDARY_TOLERANCE_M),
            axis=0,
        )
        recover = valid_boundary & ~sampled_inside & ~has_existing_member & lower_bracket & upper_bracket
        if not np.any(recover):
            continue
        # Preserve the representative-level fallback only for the same
        # bracketed recovery case. A valid boundary alone is not enough to
        # assign an unobserved pressure slice.
        representative_level = {
            "high": 300,
            "middle": 500,
            "low": 850,
        }[family]
        representative_indices = np.flatnonzero(level_values == representative_level)
        if representative_indices.size:
            representative = int(representative_indices[0])
            missing_representative = recover & ~np.isfinite(resolved[representative])
            resolved[representative] = np.where(
                missing_representative,
                aggregate,
                resolved[representative],
            )
        midpoint = 0.5 * (direct_base + direct_top)
        distances = np.where(
            above_ground[family_indices],
            np.abs(heights[family_indices] - midpoint[None, :]),
            np.inf,
        )
        nearest = family_indices[np.argmin(distances, axis=0)]
        nearest_values = resolved[nearest, columns]
        resolved[nearest, columns] = np.where(
            recover,
            np.where(np.isfinite(nearest_values), np.maximum(nearest_values, aggregate), aggregate),
            nearest_values,
        )
    return resolved


def _smooth_gate_array(
    values: np.ndarray,
    low: float,
    high: float,
) -> np.ndarray:
    """Vector form of the cubic gate used for continuous physical evidence."""
    array = np.nan_to_num(np.asarray(values, dtype=np.float32), nan=0.0)
    ratio = np.clip((array - float(low)) / max(float(high - low), 1e-6), 0.0, 1.0)
    return (ratio * ratio * (3.0 - 2.0 * ratio)).astype(np.float32)


def _aggregate_deep_cloud_risk(
    target_amount: np.ndarray,
    pressure_levels: np.ndarray,
    middle_fraction_percent: np.ndarray,
    high_fraction_percent: np.ndarray,
    boundary_heights: tuple[
        tuple[np.ndarray, np.ndarray],
        tuple[np.ndarray, np.ndarray],
        tuple[np.ndarray, np.ndarray],
    ],
    precipitation_rate_kg_m2_s: np.ndarray,
) -> np.ndarray:
    """Derive one deep-cloud material signal from a resolved layer envelope.

    Pressure-level TCC is a sample at a level centre, while MCC/HCC describe
    the union of a whole layer.  When the sampled middle layer is sparse, a
    valid aggregate boundary and precipitation provide independent evidence
    that the missing part is a continuous, optically important cloud body.
    The result is a vertical-material diagnostic only: it never changes the
    horizontal cloud amount or adds a second weather gate.
    """
    amount = np.clip(np.nan_to_num(np.asarray(target_amount, dtype=np.float32), nan=0.0), 0.0, 1.0)
    levels = np.asarray(pressure_levels, dtype=np.float32)
    high_support = np.max(
        np.where(levels <= _HIGH_CLOUD_MAX_PRESSURE_HPA, amount, 0.0),
        axis=-1,
    )
    middle_support = np.max(
        np.where(
            (levels > _HIGH_CLOUD_MAX_PRESSURE_HPA)
            & (levels <= _MID_CLOUD_MAX_PRESSURE_HPA),
            amount,
            0.0,
        ),
        axis=-1,
    )
    middle_fraction = np.clip(
        np.nan_to_num(np.asarray(middle_fraction_percent, dtype=np.float32), nan=0.0)
        / 100.0,
        0.0,
        1.0,
    )
    high_fraction = np.clip(
        np.nan_to_num(np.asarray(high_fraction_percent, dtype=np.float32), nan=0.0)
        / 100.0,
        0.0,
        1.0,
    )
    middle_base, middle_top = boundary_heights[1]
    high_base, high_top = boundary_heights[0]
    middle_base = np.asarray(middle_base, dtype=np.float32)
    middle_top = np.asarray(middle_top, dtype=np.float32)
    high_base = np.asarray(high_base, dtype=np.float32)
    high_top = np.asarray(high_top, dtype=np.float32)
    middle_valid = (
        np.isfinite(middle_base)
        & np.isfinite(middle_top)
        & (middle_top > middle_base)
    )
    high_valid = (
        np.isfinite(high_base)
        & np.isfinite(high_top)
        & (high_top > high_base)
    )
    middle_depth = np.where(
        middle_valid,
        np.maximum(middle_top - middle_base, 0.0),
        0.0,
    )
    # A layer several kilometres thick is the physical transition from a
    # thin translucent deck to a deep cloud body. These bounds match the
    # existing pressure-profile thickness scale and are not score calibration.
    depth_signal = _smooth_gate_array(middle_depth, 1500.0, 5000.0)
    precipitation_mm_h = (
        np.maximum(
            np.nan_to_num(
                np.asarray(precipitation_rate_kg_m2_s, dtype=np.float32),
                nan=0.0,
            ),
            0.0,
        )
        * 3600.0
    )
    precipitation_signal = _smooth_gate_array(precipitation_mm_h, 0.10, 1.00)
    pressure_support = (
        (high_support > ACTIVE_CLOUD_THRESHOLD)
        & (middle_support > ACTIVE_CLOUD_THRESHOLD)
    )
    aggregate_support = (
        (middle_fraction >= 0.60)
        & (high_fraction >= 0.60)
        & pressure_support
        & middle_valid
        & high_valid
    )
    # The aggregate is a horizontal union. Only the portion absent from the
    # pressure samples supplies new evidence; resolved pressure cloud remains
    # represented by the base vertical classifier.
    missing_fraction = np.divide(
        np.maximum(middle_fraction - middle_support, 0.0),
        np.maximum(1.0 - middle_support, 1e-6),
    )
    return np.where(
        aggregate_support,
        np.clip(
            middle_fraction
            * np.clip(missing_fraction, 0.0, 1.0)
            * depth_signal
            * precipitation_signal,
            0.0,
            1.0,
        ),
        0.0,
    ).astype(np.float32)


def _sparse_upper_high_support(
    levels: Sequence[int],
    fractions: np.ndarray,
    rhs: np.ndarray,
    high_layer_fraction: np.ndarray,
    liquid: np.ndarray,
    ice: np.ndarray,
) -> np.ndarray:
    """Return one conservative 300 hPa support amount for sparse high TCC.

    HCC is a union over the complete high-cloud layer, so it is not valid to
    paste it onto every pressure level. Conversely, a valid zero at one
    pressure level must not erase a real but sparsely sampled high deck. The
    caller places any returned support only at 300 hPa.
    """
    values = np.asarray(fractions, dtype=np.float32)
    humidity = np.asarray(rhs, dtype=np.float32)
    level_values = np.asarray(levels, dtype=np.float32)
    high_mask = level_values <= _HIGH_CLOUD_MAX_PRESSURE_HPA
    condensate_liquid = np.asarray(liquid, dtype=np.float32)
    condensate_ice = np.asarray(ice, dtype=np.float32)
    if (
        values.ndim != 2
        or humidity.shape != values.shape
        or condensate_liquid.shape != values.shape
        or condensate_ice.shape != values.shape
        or np.count_nonzero(high_mask) < 2
    ):
        return np.zeros_like(np.asarray(high_layer_fraction, dtype=np.float32))
    member_mask = (
        high_mask[:, None]
        & np.isfinite(values)
        & (values >= ACTIVE_CLOUD_THRESHOLD * 100.0)
    )
    member_amount = np.max(np.where(member_mask, values, 0.0), axis=0)
    member_count = np.count_nonzero(member_mask, axis=0)
    member_rh = np.max(
        np.where(member_mask, np.nan_to_num(humidity, nan=0.0), 0.0),
        axis=0,
    )
    member_condensate = np.max(
        np.where(
            member_mask,
            np.maximum(
                np.nan_to_num(condensate_liquid, nan=0.0),
                np.nan_to_num(condensate_ice, nan=0.0),
            ),
            0.0,
        ),
        axis=0,
    )
    representative_member_count = np.count_nonzero(
        member_mask & (level_values[:, None] == 300.0),
        axis=0,
    )
    aggregate = np.nan_to_num(
        np.asarray(high_layer_fraction, dtype=np.float32), nan=0.0
    )
    sparse = (
        (aggregate >= _SPARSE_UPPER_HIGH_MIN_AGGREGATE_PERCENT)
        & (aggregate <= _SPARSE_UPPER_HIGH_MAX_AGGREGATE_PERCENT)
        & (member_amount > 0.0)
        & (member_amount <= _SPARSE_UPPER_HIGH_MAX_MEMBER_PERCENT)
        & (
            member_amount
            < aggregate * _SPARSE_UPPER_HIGH_MAX_MEMBER_RATIO
        )
        & (member_rh >= _SPARSE_UPPER_HIGH_MIN_MEMBER_RH_PERCENT)
        & (member_condensate >= _SPARSE_UPPER_HIGH_MIN_CONDENSATE_KG_KG)
    )
    # A single 300 hPa pressure sample can be the only resolved slice of a real
    # high deck. When the aggregate HCC is substantial and that representative
    # slice has saturated humidity plus condensate support, retain one
    # conservative representative instead of applying generic isolated-layer
    # suppression. This is representative-layer alignment, not a new score
    # deduction; the aggregate is used once at 300 hPa and never copied to
    # every pressure level. A lone 200/250 hPa member keeps its own fraction.
    isolated_aggregate = (
        (aggregate >= _ISOLATED_AGGREGATE_HIGH_MIN_PERCENT)
        & (aggregate <= _ISOLATED_AGGREGATE_HIGH_MAX_PERCENT)
        & (member_count == 1)
        & (representative_member_count == 1)
        & (member_amount >= _ISOLATED_AGGREGATE_HIGH_MIN_MEMBER_PERCENT)
        & (member_amount < aggregate * _ISOLATED_AGGREGATE_HIGH_MAX_MEMBER_RATIO)
        & (member_rh >= _ISOLATED_AGGREGATE_HIGH_MIN_RH_PERCENT)
        & (member_condensate >= _ISOLATED_AGGREGATE_HIGH_MIN_CONDENSATE_KG_KG)
    )
    return np.where(
        isolated_aggregate,
        aggregate,
        np.where(
            sparse,
            aggregate * _SPARSE_UPPER_HIGH_SUPPORT_FACTOR,
            0.0,
        ),
    ).astype(np.float32)


def _coherent_sparse_upper_high_support(
    support: np.ndarray,
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    *,
    minimum_neighbors: int = 2,
) -> np.ndarray:
    """Keep sparse support in a small spatial patch for the regional field."""
    values = np.asarray(support, dtype=np.float32)
    lats = np.asarray(latitudes, dtype=np.float32)
    lons = np.asarray(longitudes, dtype=np.float32)
    unique_lats = np.unique(lats)
    unique_lons = np.unique(lons)
    if (
        values.ndim != 1
        or lats.shape != values.shape
        or lons.shape != values.shape
        or unique_lats.size * unique_lons.size != values.size
        or unique_lats.size < 2
        or unique_lons.size < 2
    ):
        return values
    row_indices = np.searchsorted(unique_lats, lats)
    col_indices = np.searchsorted(unique_lons, lons)
    mask = values > 0.0
    matrix = np.zeros((unique_lats.size, unique_lons.size), dtype=bool)
    matrix[row_indices, col_indices] = mask
    padded = np.pad(matrix, 1)
    neighbors = np.zeros_like(matrix, dtype=np.int16)
    for row_offset in range(3):
        for col_offset in range(3):
            neighbors += padded[
                row_offset:row_offset + matrix.shape[0],
                col_offset:col_offset + matrix.shape[1],
            ]
    neighbors -= matrix
    coherent = mask & (neighbors[row_indices, col_indices] >= minimum_neighbors)
    return np.where(coherent, values, 0.0).astype(np.float32)


def _presence(rh: np.ndarray, fraction: np.ndarray, liquid: np.ndarray, ice: np.ndarray) -> np.ndarray:
    total = np.maximum(np.nan_to_num(liquid, nan=0.0), 0.0) + np.maximum(np.nan_to_num(ice, nan=0.0), 0.0)
    condensate = np.where(total > 0.0, np.clip((np.log10(np.maximum(total, 1e-12)) + 8.0) / 3.0, 0.0, 1.0), 0.0)
    has_condensate = np.isfinite(liquid) | np.isfinite(ice)
    explicit = np.where(
        np.isfinite(fraction),
        np.clip(fraction / 100.0, 0.0, 1.0),
        np.where(has_condensate, condensate, np.nan),
    )
    # RH without a model cloud fraction or condensate is not a cloud. This is
    # the important distinction between a humid/grey background and a cloud
    # column that can occupy the sky.
    return np.where(np.isfinite(explicit), np.clip(explicit, 0.0, 1.0), 0.0).astype(np.float32)


def _blocker_opacity(
    rh: np.ndarray,
    fraction: np.ndarray,
    liquid: np.ndarray,
    ice: np.ndarray,
) -> np.ndarray:
    """Estimate actual light blocking without treating ordinary humidity as cloud."""
    rh_opacity = np.clip((np.nan_to_num(rh, nan=0.0) - 82.0) / 16.0, 0.0, 1.0)
    total = np.maximum(np.nan_to_num(liquid, nan=0.0), 0.0) + np.maximum(np.nan_to_num(ice, nan=0.0), 0.0)
    condensate = np.where(
        total > 0.0,
        np.clip((np.log10(np.maximum(total, 1e-12)) + 8.0) / 3.0, 0.0, 1.0),
        0.0,
    )
    has_condensate = np.isfinite(liquid) | np.isfinite(ice)
    # The resolved fraction is horizontal coverage, not the optical depth of
    # the cloudy part. Keep that coverage in ``amount`` and let native ray
    # tracing combine it with this per-cloud optical opacity exactly once.
    explicit = np.where(
        np.isfinite(fraction),
        0.35 + 0.65 * condensate,
        np.where(has_condensate, condensate, np.nan),
    )
    explicit_opacity = np.clip(explicit, 0.0, 1.0)
    blended_opacity = np.clip(0.22 * rh_opacity + 0.78 * explicit_opacity, 0.0, 1.0)
    return np.where(
        np.isfinite(fraction),
        np.where(
            fraction > 0.0,
            blended_opacity,
            0.0,
        ),
        np.where(
            has_condensate,
            np.clip(0.22 * rh_opacity + 0.78 * np.clip(condensate, 0.0, 1.0), 0.0, 1.0),
            0.0,
        ),
    ).astype(np.float32)


def _effective_profile_cloud_fields(
    profile: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return target amount, optical amount, opacity, fraction and explicit mask.

    A valid model fraction is the authoritative horizontal coverage. Relative
    humidity remains a fallback only when the model has no fraction or
    condensate field for that level.
    """
    values = np.asarray(profile, dtype=np.float32)
    pressure = values[..., _PROFILE_PRESSURE]
    presence = np.clip(np.nan_to_num(values[..., _PROFILE_PRESENCE], nan=0.0), 0.0, 1.0)
    fraction = values[..., _PROFILE_FRACTION]
    high_fraction = values[..., _PROFILE_HIGH_FRACTION]
    resolved_fraction = np.where(
        np.isfinite(fraction),
        fraction,
        np.where(np.rint(pressure) == 300.0, high_fraction, fraction),
    )
    liquid = values[..., _PROFILE_LIQUID]
    ice = values[..., _PROFILE_ICE]
    opacity = _blocker_opacity(
        values[..., _PROFILE_RH],
        resolved_fraction,
        liquid,
        ice,
    )
    finite_fraction = np.isfinite(resolved_fraction)
    raw_amount = np.clip(np.nan_to_num(resolved_fraction, nan=0.0) / 100.0, 0.0, 1.0)
    # A GFS cloud fraction is horizontal coverage. Keep it independent from
    # the cloudy-part opacity: opacity is consumed by the native light-path
    # blocker and the material diagnostics, so folding it into the amount here
    # would attenuate the same cloud twice and make profile diagnostics diverge
    # from the authoritative regional field.
    optical_amount = np.where(finite_fraction, raw_amount, presence)
    explicit = finite_fraction | np.isfinite(liquid) | np.isfinite(ice)
    target_amount = np.where(explicit, optical_amount, presence)
    return (
        np.asarray(target_amount, dtype=np.float32),
        np.asarray(optical_amount, dtype=np.float32),
        np.asarray(opacity, dtype=np.float32),
        np.asarray(resolved_fraction, dtype=np.float32),
        np.asarray(explicit, dtype=bool),
    )


def _resolved_family_body_deep_risks(
    target_amount: np.ndarray,
    pressure_levels: np.ndarray,
    aggregate_deep_risk: np.ndarray,
    family_members: tuple[np.ndarray, np.ndarray, np.ndarray],
    family_amount: tuple[np.ndarray, np.ndarray, np.ndarray],
    family_base_m: tuple[np.ndarray, np.ndarray, np.ndarray],
    family_top_m: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply the legacy deep scalar to each resolved target cloud body."""
    amount = np.clip(
        np.nan_to_num(np.asarray(target_amount, dtype=np.float32), nan=0.0),
        0.0,
        1.0,
    )
    levels = np.asarray(pressure_levels, dtype=np.float32)
    aggregate = np.clip(
        np.nan_to_num(np.asarray(aggregate_deep_risk, dtype=np.float32), nan=0.0),
        0.0,
        1.0,
    )
    members_by_family = tuple(np.asarray(mask, dtype=bool) for mask in family_members)
    amounts_by_family = tuple(
        np.clip(np.nan_to_num(np.asarray(value, dtype=np.float32), nan=0.0), 0.0, 1.0)
        for value in family_amount
    )
    bases_by_family = tuple(np.asarray(value, dtype=np.float32) for value in family_base_m)
    tops_by_family = tuple(np.asarray(value, dtype=np.float32) for value in family_top_m)
    risks: list[np.ndarray] = []

    for family_index in range(3):
        body_members = members_by_family[family_index].copy()
        target_base = bases_by_family[family_index]
        target_top = tops_by_family[family_index]
        connected_high_mid = np.zeros_like(target_base, dtype=bool)
        for adjacent_index in range(3):
            if abs(adjacent_index - family_index) != 1:
                continue
            connected = (
                (amounts_by_family[family_index] >= _MIN_CONNECTED_TARGET_DECK_AMOUNT)
                & (amounts_by_family[adjacent_index] >= _MIN_CONNECTED_TARGET_DECK_AMOUNT)
                & np.isfinite(target_base)
                & np.isfinite(target_top)
                & np.isfinite(bases_by_family[adjacent_index])
                & np.isfinite(tops_by_family[adjacent_index])
                & (
                    target_top
                    >= bases_by_family[adjacent_index]
                    - _CLOUD_FAMILY_CONNECTION_TOLERANCE_M
                )
                & (
                    target_base
                    <= tops_by_family[adjacent_index]
                    + _CLOUD_FAMILY_CONNECTION_TOLERANCE_M
                )
            )
            if {family_index, adjacent_index} == {0, 1}:
                connected_high_mid |= connected
            body_members |= members_by_family[adjacent_index] & connected[None, :]

        body_amount = np.where(body_members, amount, 0.0)
        body_family_masks = tuple(
            mask & body_members for mask in members_by_family
        )
        deep_structure = _vertical_cloud_deep_structure(
            body_amount.T,
            levels,
            np.where(
                (family_index < 2) & connected_high_mid,
                aggregate,
                0.0,
            ),
            _resolved_family_masks=tuple(mask.T for mask in body_family_masks),
        )
        risks.append(np.asarray(deep_structure.deep, dtype=np.float32))
    return tuple(risks)  # type: ignore[return-value]


class _VerticalCloudDeepStructure(NamedTuple):
    amount: np.ndarray
    levels: np.ndarray
    high_mask: np.ndarray
    mid_mask: np.ndarray
    low_mask: np.ndarray
    resolved_family_masks: tuple[np.ndarray, np.ndarray, np.ndarray]
    high: np.ndarray
    mid: np.ndarray
    low: np.ndarray
    structure_amount: np.ndarray
    structure_high: np.ndarray
    structure_mid: np.ndarray
    base_mid: np.ndarray
    structure_low: np.ndarray
    cloudy: np.ndarray
    deep: np.ndarray


def _vertical_cloud_deep_structure(
    target_amount: np.ndarray,
    pressure_levels: np.ndarray,
    aggregate_deep_risk: np.ndarray | None = None,
    *,
    _resolved_family_masks: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
) -> _VerticalCloudDeepStructure:
    amount = np.clip(np.nan_to_num(target_amount, nan=0.0), 0.0, 1.0)
    levels = np.asarray(pressure_levels, dtype=np.float32)
    upper_support = np.max(
        np.where(levels <= _ISOLATED_UPPER_HIGH_MAX_PRESSURE_HPA, amount, 0.0),
        axis=-1,
        keepdims=True,
    )
    lower_middle_support = np.max(
        np.where(
            (levels > _HIGH_CLOUD_MAX_PRESSURE_HPA)
            & (levels <= _MID_CLOUD_MAX_PRESSURE_HPA),
            amount,
            0.0,
        ),
        axis=-1,
        keepdims=True,
    )
    boundary_400_amount = np.max(
        np.where(levels == _HIGH_CLOUD_MAX_PRESSURE_HPA, amount, 0.0),
        axis=-1,
        keepdims=True,
    )
    has_upper_pair = bool(
        np.any(levels == 250.0) and np.any(levels == 300.0)
    )
    upper_pair_support = np.min(
        np.where(
            (levels == 250.0) | (levels == 300.0),
            amount,
            1.0,
        ),
        axis=-1,
        keepdims=True,
    )
    if not has_upper_pair:
        upper_pair_support = np.zeros_like(boundary_400_amount)
    continuous_upper_400 = (
        (boundary_400_amount >= _CONTINUOUS_UPPER_400_MIN_AMOUNT)
        & (
            (lower_middle_support < _CONTINUOUS_UPPER_400_MAX_LOWER_MIDDLE_AMOUNT)
            | (upper_pair_support >= _CONTINUOUS_UPPER_400_MIN_UPPER_PAIR_AMOUNT)
        )
    )
    adaptive_middle_400 = (
        (levels == _HIGH_CLOUD_MAX_PRESSURE_HPA)
        & (upper_support >= _ADAPTIVE_MIDDLE_400_UPPER_SUPPORT_THRESHOLD)
        & ~continuous_upper_400
    )
    high_mask = (
        (levels <= _HIGH_CLOUD_MAX_PRESSURE_HPA)
        & ~adaptive_middle_400
    )
    mid_mask = (
        ((levels > _HIGH_CLOUD_MAX_PRESSURE_HPA) & (levels <= _MID_CLOUD_MAX_PRESSURE_HPA))
        | adaptive_middle_400
    )
    low_mask = levels > _MID_CLOUD_MAX_PRESSURE_HPA
    resolved_family_masks = (high_mask, mid_mask, low_mask)
    if _resolved_family_masks is not None:
        high_mask, mid_mask, low_mask = tuple(
            np.asarray(mask, dtype=bool) for mask in _resolved_family_masks
        )
    high = np.max(np.where(high_mask, amount, 0.0), axis=-1)
    mid = np.max(np.where(mid_mask, amount, 0.0), axis=-1)
    low = np.max(np.where(low_mask, amount, 0.0), axis=-1)
    # Horizontal cloud fraction controls sky area elsewhere. A vertically
    # coherent high deck therefore uses its relative profile for material
    # classification, so sparse cirrus is not deducted once as area and again
    # as poor material. Diffuse high profiles and mid/low decks keep absolute
    # amounts because their weak vertical support is meaningful evidence.
    peak_amount = np.max(amount, axis=-1, keepdims=True)
    normalized_structure_amount = np.divide(
        amount,
        peak_amount,
        out=np.zeros_like(amount),
        where=peak_amount > ACTIVE_CLOUD_THRESHOLD,
    )
    relative_support_count = np.count_nonzero(
        normalized_structure_amount >= 0.20, axis=-1
    )
    peak_amount_flat = peak_amount[..., 0]
    high_is_dominant = (
        (high > ACTIVE_CLOUD_THRESHOLD)
        & np.isclose(high, peak_amount_flat, rtol=0.0, atol=1e-6)
    )
    normalize_coherent_high_deck = high_is_dominant & (relative_support_count <= 2)
    structure_amount = np.where(
        normalize_coherent_high_deck[..., None],
        normalized_structure_amount,
        amount,
    )
    structure_high = np.max(
        np.where(high_mask, structure_amount, 0.0), axis=-1
    )
    structure_mid = np.max(
        np.where(mid_mask, structure_amount, 0.0), axis=-1
    )
    # Keep the cloud-type label from treating the boundary sample alone as a
    # fully developed middle deck. The 400 hPa sample still participates in
    # ray geometry and deep-risk diagnostics, but a genuine high+middle type
    # requires support below that boundary as well.
    base_mid_mask = (levels > _HIGH_CLOUD_MAX_PRESSURE_HPA) & (
        levels <= _MID_CLOUD_MAX_PRESSURE_HPA
    )
    base_mid = np.max(
        np.where(base_mid_mask, structure_amount, 0.0), axis=-1
    )
    structure_low = np.max(
        np.where(low_mask, structure_amount, 0.0), axis=-1
    )
    cloudy = structure_amount >= _PROFILE_DECK_MEMBER_AMOUNT
    cloudy_count = np.count_nonzero(cloudy, axis=-1).astype(np.float32)
    deep = np.clip(
        0.45 * np.minimum(structure_high, structure_mid)
        + 0.35 * np.minimum(structure_mid, structure_low)
        + 0.20 * cloudy_count / 5.0,
        0.0,
        1.0,
    )
    if aggregate_deep_risk is not None:
        deep = np.maximum(
            deep,
            np.clip(
                np.nan_to_num(np.asarray(aggregate_deep_risk, dtype=np.float32), nan=0.0),
                0.0,
                1.0,
            ),
        )
    return _VerticalCloudDeepStructure(
        amount,
        levels,
        high_mask,
        mid_mask,
        low_mask,
        resolved_family_masks,
        high,
        mid,
        low,
        structure_amount,
        structure_high,
        structure_mid,
        base_mid,
        structure_low,
        cloudy,
        deep,
    )


def _vertical_cloud_structure(
    target_amount: np.ndarray,
    pressure_levels: np.ndarray,
    heights_m: np.ndarray,
    liquid: np.ndarray,
    ice: np.ndarray,
    omega: np.ndarray,
    explicit_fields: np.ndarray,
    aggregate_deep_risk: np.ndarray | None = None,
    *,
    _resolved_family_masks: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    _derive_layer_materials: bool = True,
) -> tuple[np.ndarray, ...]:
    """Classify one or more pressure profiles and derive cloud material quality.

    All profile-like inputs use ``(..., pressure_level)``. Cloud-family bounds
    are mutually exclusive so 500/700 hPa cannot simultaneously create two
    layers. The column material remains an internal connected-deep cap; the
    final three values are family-isolated material signals.
    """
    deep_structure = _vertical_cloud_deep_structure(
        target_amount,
        pressure_levels,
        aggregate_deep_risk,
        _resolved_family_masks=_resolved_family_masks,
    )
    (
        amount,
        levels,
        high_mask,
        mid_mask,
        low_mask,
        resolved_family_masks,
        high,
        mid,
        low,
        structure_amount,
        structure_high,
        structure_mid,
        base_mid,
        structure_low,
        cloudy,
        deep,
    ) = deep_structure

    heights = np.asarray(heights_m, dtype=np.float32)
    lowest = np.min(np.where(cloudy, heights, np.inf), axis=-1)
    highest = np.max(np.where(cloudy, heights, -np.inf), axis=-1)
    depth_m = np.where(
        np.isfinite(lowest) & np.isfinite(highest),
        np.maximum(highest - lowest, 0.0),
        0.0,
    )
    thickness = np.clip((depth_m - 2800.0) / 5200.0, 0.0, 1.0)

    liquid_values = np.maximum(np.nan_to_num(liquid, nan=0.0), 0.0)
    ice_values = np.maximum(np.nan_to_num(ice, nan=0.0), 0.0)
    liquid_presence = np.max(
        np.where(
            liquid_values > 0.0,
            np.clip((np.log10(np.maximum(liquid_values, 1e-12)) + 8.0) / 3.0, 0.0, 1.0),
            0.0,
        ),
        axis=-1,
    )
    ice_presence = np.max(
        np.where(
            ice_values > 0.0,
            np.clip((np.log10(np.maximum(ice_values, 1e-12)) + 8.0) / 3.0, 0.0, 1.0),
            0.0,
        ),
        axis=-1,
    )
    has_explicit = np.any(np.asarray(explicit_fields, dtype=bool), axis=-1)
    omega_values = np.asarray(omega, dtype=np.float32)
    strongest_lift = np.min(
        np.where(np.isfinite(omega_values), omega_values, np.inf), axis=-1
    )
    strongest_lift = np.where(np.isfinite(strongest_lift), strongest_lift, 0.0)
    strongest_high = np.any(high_mask & (structure_amount >= 0.45), axis=-1)
    cloud_material_class_code = np.select(
        [
            (deep >= 0.68) & (structure_low >= 0.45),
            (deep >= 0.58)
            & (strongest_lift <= -0.25)
            # A cumulonimbus anvil still has a low-cloud base.  Liquid water
            # in a 400/500 hPa sample is not sufficient evidence by itself:
            # deep As/Ac decks commonly carry liquid water while their low
            # family is clear.  Promoting those columns to Cb incus makes the
            # target genus and its material penalty disagree with the
            # sounding-style Bot diagnosis.
            & (structure_low >= 0.20)
            & (~has_explicit | (ice_presence >= 0.12)),
            (structure_low >= 0.62)
            & (structure_high < 0.35)
            & (structure_mid < 0.45),
            (structure_mid >= 0.58)
            & (structure_high < 0.45)
            & (structure_low < 0.45),
            (structure_high >= 0.55) & (base_mid >= 0.45),
            (structure_high >= 0.50)
            & strongest_high
            & (~has_explicit | (ice_presence >= np.maximum(0.12, liquid_presence * 0.65))),
            structure_mid >= 0.45,
            structure_low >= 0.45,
        ],
        [8, 4, 6, 2, 3, 1, 5, 7],
        default=0,
    ).astype(np.uint8)

    # A validated aggregate middle-layer envelope can make an otherwise
    # cirrus-looking pressure sample materially deep.  Keep that column as a
    # layered high cloud proxy; without low-cloud or liquid-water support it
    # must not be promoted to a cumulonimbus anvil.
    aggregate_layered_high = (
        (cloud_material_class_code == 1)
        & (np.asarray(aggregate_deep_risk) >= 0.25)
        & (structure_high >= 0.45)
    ) if aggregate_deep_risk is not None else np.zeros_like(cloud_material_class_code, dtype=bool)
    cloud_material_class_code = np.where(aggregate_layered_high, 3, cloud_material_class_code).astype(np.uint8)

    # A high cloud without enough ice-phase evidence is high-layer cloud, not
    # cirrus. Keep this branch after the cirrus selector so it does not turn a
    # resolved ice deck into a generic high layer.
    high_layer_without_phase = (
        (cloud_material_class_code == 0)
        & (structure_high >= 0.50)
        & strongest_high
    )
    cloud_material_class_code = np.where(high_layer_without_phase, 3, cloud_material_class_code).astype(np.uint8)

    column_material = _vertical_cloud_material(
        structure_high,
        structure_mid,
        structure_low,
        deep,
        thickness,
        cloud_material_class_code,
    )
    if not _derive_layer_materials:
        return (
            high,
            mid,
            low,
            deep,
            thickness,
            cloud_material_class_code,
            column_material,
            np.zeros_like(column_material),
            np.zeros_like(column_material),
            np.zeros_like(column_material),
        )

    layer_materials: list[np.ndarray] = []
    empty_mask = np.zeros_like(resolved_family_masks[0], dtype=bool)
    for family_index, family_mask in enumerate(resolved_family_masks):
        family_mask = np.asarray(family_mask, dtype=bool)
        masked_amount = np.where(family_mask, amount, 0.0)
        masked_liquid = np.where(family_mask, liquid_values, 0.0)
        masked_ice = np.where(family_mask, ice_values, 0.0)
        masked_omega = np.where(family_mask, omega_values, 0.0)
        masked_explicit = np.asarray(explicit_fields, dtype=bool) & family_mask
        isolated = _vertical_cloud_structure(
            masked_amount,
            levels,
            heights,
            masked_liquid,
            masked_ice,
            masked_omega,
            masked_explicit,
            None,
            _resolved_family_masks=tuple(
                family_mask if index == family_index else empty_mask
                for index in range(3)
            ),
            _derive_layer_materials=False,
        )
        family_amount = isolated[family_index]
        layer_materials.append(
            np.where(
                family_amount > ACTIVE_CLOUD_THRESHOLD,
                isolated[6],
                0.0,
            ).astype(np.float32)
        )
    return (
        high,
        mid,
        low,
        deep,
        thickness,
        cloud_material_class_code,
        column_material,
        layer_materials[0],
        layer_materials[1],
        layer_materials[2],
    )


def _vertical_cloud_material(
    structure_high: np.ndarray,
    structure_mid: np.ndarray,
    structure_low: np.ndarray,
    deep: np.ndarray,
    thickness: np.ndarray,
    cloud_material_class_code: np.ndarray,
) -> np.ndarray:
    """Purely map resolved vertical evidence to the legacy material signal."""
    base = np.take(
        np.asarray((0.08, 0.93, 0.90, 0.80, 0.76, 0.62, 0.34, 0.22, 0.28)),
        cloud_material_class_code,
    )
    sensitivity = np.take(
        np.asarray((0.35, 0.70, 0.60, 0.50, 0.40, 0.35, 0.35, 0.25, 0.35)),
        cloud_material_class_code,
    )
    high_enough = np.clip(
        np.maximum(structure_high, 0.75 * structure_mid), 0.0, 1.0
    )
    low_penalty = np.where(
        np.isin(cloud_material_class_code, (6, 7)), 0.0, 0.28 * structure_low
    )
    thick_penalty = 0.34 * deep + 0.18 * thickness
    thick_penalty = np.where(
        np.isin(cloud_material_class_code, (1, 2)), thick_penalty * 0.55, thick_penalty
    )
    thick_penalty = np.where(cloud_material_class_code == 8, thick_penalty * 1.35, thick_penalty)
    optical = np.clip(
        1.0 - sensitivity * (0.30 * thickness + 0.20 * deep), 0.0, 1.0
    )
    return np.clip(
        base * optical * (0.55 + 0.45 * high_enough)
        - low_penalty
        - thick_penalty,
        0.0,
        1.0,
    ).astype(np.float32)


def _apply_connected_deep_material_cap(
    cloud_material_class_code: np.ndarray,
    column_cap: np.ndarray,
    high_amount: np.ndarray,
    high_base: np.ndarray,
    high_top: np.ndarray,
    mid_amount: np.ndarray,
    mid_base: np.ndarray,
    mid_top: np.ndarray,
    low_amount: np.ndarray,
    low_base: np.ndarray,
    low_top: np.ndarray,
    high_material: np.ndarray,
    mid_material: np.ndarray,
    low_material: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cap all participating layers only for a fully connected deep column."""

    def connected_families(
        first_amount: np.ndarray,
        first_base: np.ndarray,
        first_top: np.ndarray,
        second_amount: np.ndarray,
        second_base: np.ndarray,
        second_top: np.ndarray,
    ) -> np.ndarray:
        return (
            (first_amount >= _MIN_CONNECTED_TARGET_DECK_AMOUNT)
            & (second_amount >= _MIN_CONNECTED_TARGET_DECK_AMOUNT)
            & np.isfinite(first_base)
            & np.isfinite(first_top)
            & np.isfinite(second_base)
            & np.isfinite(second_top)
            & (first_top >= second_base - _CLOUD_FAMILY_CONNECTION_TOLERANCE_M)
            & (first_base <= second_top + _CLOUD_FAMILY_CONNECTION_TOLERANCE_M)
        )

    high_mid_connected = connected_families(
        high_amount, high_base, high_top, mid_amount, mid_base, mid_top
    )
    mid_low_connected = connected_families(
        mid_amount, mid_base, mid_top, low_amount, low_base, low_top
    )
    connected_deep = (
        np.isin(cloud_material_class_code, (4, 8))
        & high_mid_connected
        & mid_low_connected
    )
    return tuple(
        np.where(
            connected_deep,
            np.minimum(material, column_cap),
            material,
        ).astype(np.float32)
        for material in (high_material, mid_material, low_material)
    )


def derive_grid_layer_profiles(
    grid: CanonicalRegionalGrid,
    *,
    _prepared: _GridDerivationInputs | None = None,
) -> tuple[np.ndarray, tuple[int, ...]]:
    """Retain per-cell, per-pressure-level cloud inputs for later ray tracing."""
    prepared = (
        _prepared
        if _prepared is not None
        else _current_grid_derivation_inputs(grid)
    )
    if prepared is None:
        prepared = _prepare_grid_derivation(grid)
    levels = prepared.levels
    rows: list[np.ndarray] = []
    surface_elevation = prepared.surface_elevation
    high_layer_fraction = prepared.high_layer_fraction
    high_fraction_semantics = prepared.high_fraction_semantics
    middle_layer_fraction = prepared.middle_layer_fraction
    low_layer_fraction = prepared.low_layer_fraction
    instant_layer_fractions = prepared.instant_layer_fractions
    heights = prepared.heights
    temperatures = prepared.temperatures
    aggregate_boundaries = prepared.boundary_heights
    resolved_fractions = prepared.resolved_fractions
    sparse_high_support = prepared.sparse_high_support
    omega = prepared.omega
    u_winds = prepared.u_winds
    v_winds = prepared.v_winds
    high_deep = prepared.high_deep
    high_total = prepared.high_total
    high_thin = prepared.high_thin
    for level_index, level in enumerate(levels):
        rh = prepared.raw_rhs[level_index]
        height = prepared.heights[level_index]
        fraction = resolved_fractions[level_index]
        high_fraction = high_layer_fraction
        # HCDC/HCC is the aggregate amount for the whole high-cloud layer,
        # not the cloud fraction at 300 hPa.  Using max() here turns a thin
        # pressure-level target into a broad opaque deck.  Keep the pressure
        # layer's own fraction when it exists and use the aggregate only as a
        # missing-data fallback.
        if level == 300:
            sparse_mask = sparse_high_support > 0.0
            effective_fraction = np.where(
                sparse_mask,
                np.maximum(np.nan_to_num(fraction, nan=0.0), sparse_high_support),
                fraction,
            )
            presence_fraction = np.where(
                np.isfinite(effective_fraction),
                effective_fraction,
                np.where(
                    high_fraction_semantics == "instantaneous",
                    high_fraction,
                    np.nan,
                ),
            )
        else:
            effective_fraction = fraction
            presence_fraction = fraction
        liquid = prepared.raw_liquid[level_index]
        ice = prepared.raw_ice[level_index]
        above_ground = np.isfinite(height) & (height > surface_elevation + 25.0)
        # Preserve the raw missing-data marker in the sidecar unless the
        # sparse aggregate rule explicitly supplied a representative deck.
        resolved_fraction = np.where(
            above_ground,
            effective_fraction,
            0.0,
        )
        resolved_liquid = np.where(above_ground, liquid, 0.0)
        resolved_ice = np.where(above_ground, ice, 0.0)
        rows.append(np.stack((
            np.nan_to_num(rh, nan=0.0),
            np.nan_to_num(height, nan=0.0),
            temperatures[level_index],
            omega[level_index],
            u_winds[level_index],
            v_winds[level_index],
            resolved_fraction,
            high_fraction,
            high_deep,
            high_total,
            high_thin,
            resolved_liquid,
            resolved_ice,
            np.where(
                above_ground,
                _presence(rh, presence_fraction, liquid, ice),
                0.0,
            ),
            np.full(grid.latitudes.shape, float(level), dtype=np.float32),
            middle_layer_fraction,
            low_layer_fraction,
            aggregate_boundaries["high"][0],
            aggregate_boundaries["high"][1],
            aggregate_boundaries["middle"][0],
            aggregate_boundaries["middle"][1],
            aggregate_boundaries["low"][0],
            aggregate_boundaries["low"][1],
            surface_elevation,
        ), axis=-1))
    # (cells, pressure levels, fields), contiguous for cheap cell/column access.
    return np.ascontiguousarray(np.stack(rows, axis=1), dtype=np.float32), tuple(levels)


def derive_grid_columns(
    grid: CanonicalRegionalGrid,
    *,
    _prepared: _GridDerivationInputs | None = None,
) -> np.ndarray:
    prepared = (
        _prepared
        if _prepared is not None
        else _current_grid_derivation_inputs(grid)
    )
    if prepared is None:
        prepared = _prepare_grid_derivation(grid)
    levels = prepared.levels
    presence_rows: list[np.ndarray] = []
    height_rows: list[np.ndarray] = []
    rh_rows: list[np.ndarray] = []
    fraction_rows: list[np.ndarray] = []
    liquid_rows: list[np.ndarray] = []
    ice_rows: list[np.ndarray] = []
    omega_rows: list[np.ndarray] = []
    temperature_rows: list[np.ndarray] = []
    u_wind_rows: list[np.ndarray] = []
    v_wind_rows: list[np.ndarray] = []
    opacity_rows: list[np.ndarray] = []
    high_layer_fraction = prepared.high_layer_fraction
    middle_layer_fraction = prepared.middle_layer_fraction
    low_layer_fraction = prepared.low_layer_fraction
    high_fraction_semantics = prepared.high_fraction_semantics
    instant_layer_fractions = prepared.instant_layer_fractions
    raw_sparse_high_support = prepared.raw_sparse_high_support
    sparse_high_support = prepared.sparse_high_support
    temperatures = prepared.temperatures
    omega = prepared.omega
    u_winds = prepared.u_winds
    v_winds = prepared.v_winds
    surface_elevation = prepared.surface_elevation
    boundary_heights = prepared.boundary_heights
    resolved_fractions = prepared.resolved_fractions
    for level_index, level in enumerate(levels):
        rh = prepared.raw_rhs[level_index]
        height = prepared.heights[level_index]
        fraction = resolved_fractions[level_index]
        # Layer totals are explicit fallbacks only at representative pressure
        # levels. Copying one aggregate to every pressure slice would turn a
        # single layer into repeated cloud columns.
        fallback = np.full(grid.latitudes.shape, np.nan, dtype=np.float32)
        if level == 300:
            fallback = np.where(
                sparse_high_support > 0.0,
                sparse_high_support,
                instant_layer_fractions[0],
            )
        elif level == 500:
            fallback = instant_layer_fractions[1]
        elif level == 850:
            fallback = instant_layer_fractions[2]
        fraction = np.where(np.isfinite(fraction), fraction, fallback)
        if level == 300 and np.any(sparse_high_support > 0.0):
            fraction = np.maximum(
                np.nan_to_num(fraction, nan=0.0),
                sparse_high_support,
            )
        liquid = prepared.raw_liquid[level_index]
        ice = prepared.raw_ice[level_index]
        presence_rows.append(_presence(rh, fraction, liquid, ice))
        height_rows.append(np.nan_to_num(height, nan=0.0).astype(np.float32))
        rh_rows.append(np.nan_to_num(rh, nan=0.0).astype(np.float32))
        fraction_rows.append(fraction.astype(np.float32, copy=False))
        liquid_rows.append(liquid.astype(np.float32, copy=False))
        ice_rows.append(ice.astype(np.float32, copy=False))
        temperature_rows.append(
            temperatures[level_index]
        )
        omega_rows.append(omega[level_index])
        u_wind_rows.append(u_winds[level_index])
        v_wind_rows.append(v_winds[level_index])
        opacity_rows.append(_blocker_opacity(rh, fraction, liquid, ice))
    presence = np.stack(presence_rows)
    heights = np.stack(height_rows)
    rhs = np.stack(rh_rows)
    fractions = np.stack(fraction_rows)
    liquids = np.stack(liquid_rows)
    ices = np.stack(ice_rows)
    temperatures = np.stack(temperature_rows)
    omegas = np.stack(omega_rows)
    u_winds = np.stack(u_wind_rows)
    v_winds = np.stack(v_wind_rows)
    opacities = np.stack(opacity_rows)
    level_values = np.asarray(levels, dtype=np.float32)
    above_ground = np.isfinite(heights) & (
        heights > surface_elevation[None, :] + 25.0
    )
    presence = np.where(above_ground, presence, 0.0)
    opacities = np.where(above_ground, opacities, 0.0)

    # The GFS cloud fraction is the horizontal amount. Keep it unchanged in
    # the active field model: a valid 11% fraction must remain 11%, and RH is
    # never allowed to inflate it into a broad cloud deck.
    finite_fraction = np.isfinite(fractions)
    raw_amount = np.nan_to_num(fractions, nan=0.0) / 100.0
    cloud_amount = np.where(finite_fraction, raw_amount, presence)
    cloud_amount = np.where(above_ground, cloud_amount, 0.0)
    target_amount = cloud_amount

    base_level_families = np.where(
        level_values <= _HIGH_CLOUD_MAX_PRESSURE_HPA,
        0,
        np.where(level_values <= _MID_CLOUD_MAX_PRESSURE_HPA, 1, 2),
    )
    upper_support = np.max(
        np.where(
            level_values[:, None] <= _ISOLATED_UPPER_HIGH_MAX_PRESSURE_HPA,
            target_amount,
            0.0,
        ),
        axis=0,
    ).reshape(-1)
    lower_middle_support = np.max(
        np.where(
            (level_values[:, None] > _HIGH_CLOUD_MAX_PRESSURE_HPA)
            & (level_values[:, None] <= _MID_CLOUD_MAX_PRESSURE_HPA),
            target_amount,
            0.0,
        ),
        axis=0,
    ).reshape(-1)
    boundary_400_amount = np.max(
        np.where(
            level_values[:, None] == _HIGH_CLOUD_MAX_PRESSURE_HPA,
            target_amount,
            0.0,
        ),
        axis=0,
    ).reshape(-1)
    has_upper_pair = bool(
        np.any(level_values == 250.0) and np.any(level_values == 300.0)
    )
    upper_pair_support = np.min(
        np.where(
            (level_values[:, None] == 250.0)
            | (level_values[:, None] == 300.0),
            target_amount,
            1.0,
        ),
        axis=0,
    ).reshape(-1)
    if not has_upper_pair:
        upper_pair_support = np.zeros_like(boundary_400_amount)
    continuous_upper_400 = (
        (boundary_400_amount >= _CONTINUOUS_UPPER_400_MIN_AMOUNT)
        & (
            (lower_middle_support < _CONTINUOUS_UPPER_400_MAX_LOWER_MIDDLE_AMOUNT)
            | (upper_pair_support >= _CONTINUOUS_UPPER_400_MIN_UPPER_PAIR_AMOUNT)
        )
    )
    adaptive_middle_400 = (
        (level_values[:, None] == _HIGH_CLOUD_MAX_PRESSURE_HPA)
        & (upper_support[None, :] >= _ADAPTIVE_MIDDLE_400_UPPER_SUPPORT_THRESHOLD)
        & ~continuous_upper_400[None, :]
    )
    level_families = np.where(
        adaptive_middle_400,
        1,
        base_level_families[:, None],
    )
    # A pressure-level cloud field is a vertical sampling of a deck, not a
    # stack of independent horizontal clouds. Collapse contiguous levels once
    # within each high/middle/low family. A vertically deep cloud body may span
    # all three families; assigning that whole body to the pressure level with
    # the largest amount erases its lower-cloud opacity and produces the false
    # result "high cloud can burn through a solid low cloud curtain".
    active = np.isfinite(target_amount) & (target_amount > 0.005)
    deck_amount = np.zeros_like(target_amount, dtype=np.float32)
    family_decks: list[list[tuple[np.ndarray, np.ndarray]]] = [[], [], []]
    family_opacity = [
        np.zeros(grid.latitudes.shape, dtype=np.float32)
        for _ in range(3)
    ]
    family_amount = [
        np.zeros(grid.latitudes.shape, dtype=np.float32)
        for _ in range(3)
    ]
    local_upper_support = np.zeros(grid.latitudes.shape, dtype=np.float32)
    safe_opacities = np.nan_to_num(opacities, nan=0.0)
    for family_index in range(3):
        family_active = active & (level_families == family_index)
        starts = family_active.copy()
        if starts.shape[0] > 1:
            starts[1:] &= ~family_active[:-1]
        deck_ids = np.cumsum(starts, axis=0)
        for deck_id in range(1, len(levels) + 1):
            members = family_active & (deck_ids == deck_id)
            present = np.any(members, axis=0)
            if not np.any(present):
                continue
            amount = np.max(np.where(members, target_amount, 0.0), axis=0)
            if family_index == 0:
                aggregate_backed_deck = (
                    (sparse_high_support > 0.0)
                    & np.any(
                        members & (level_values[:, None] == 300.0),
                        axis=0,
                    )
                )
                # HCC is the union of the whole high-cloud layer, not support
                # at every pressure level. Only pressure samples carrying at
                # least 20% of the dominant level count as vertical support;
                # trace values must not turn an isolated layer into a deck.
                peak_member_amount = np.max(
                    np.where(members, target_amount, 0.0), axis=0
                )
                supported_members = members & (
                    target_amount
                    >= np.maximum(
                        ACTIVE_CLOUD_THRESHOLD,
                        _UPPER_HIGH_RELATIVE_SUPPORT_THRESHOLD
                        * peak_member_amount,
                    )[None, :]
                )
                member_count = np.count_nonzero(supported_members, axis=0)
                deck_rh = np.max(np.where(supported_members, rhs, 0.0), axis=0)
                deck_pressure = np.max(
                    np.where(supported_members, level_values[:, None], 0.0),
                    axis=0,
                )
                saturation_confidence = np.clip(
                    (
                        deck_rh
                        - _ISOLATED_UPPER_HIGH_SATURATION_RH_PERCENT
                    )
                    / (100.0 - _ISOLATED_UPPER_HIGH_SATURATION_RH_PERCENT),
                    0.0,
                    1.0,
                )
                upper_only = (
                    deck_pressure <= _ISOLATED_UPPER_HIGH_MAX_PRESSURE_HPA
                )
                upper_confidence = np.where(
                    member_count == 1,
                    np.where(
                        deck_rh >= _ISOLATED_UPPER_HIGH_SATURATION_RH_PERCENT,
                        np.clip(
                            _ISOLATED_UPPER_HIGH_SATURATION_FACTOR_FLOOR
                            + _ISOLATED_UPPER_HIGH_SATURATION_FACTOR_WEIGHT
                            * saturation_confidence,
                            0.0,
                            _ISOLATED_UPPER_HIGH_MAX_AMOUNT_FACTOR,
                        ),
                        _ISOLATED_UPPER_HIGH_SATURATION_FACTOR_FLOOR,
                    ),
                    np.where(
                        (member_count == 2)
                        & (
                            deck_pressure
                            <= _TWO_LEVEL_UPPER_HIGH_MAX_PRESSURE_HPA
                        ),
                        _UPPER_HIGH_MIN_SUPPORT_FACTOR
                        + _UPPER_HIGH_SUPPORT_FACTOR_WEIGHT
                        * np.divide(
                            np.min(
                                np.where(
                                    supported_members,
                                    target_amount,
                                    np.inf,
                                ),
                                axis=0,
                            ),
                            peak_member_amount,
                            out=np.zeros_like(peak_member_amount),
                            where=peak_member_amount > 0.0,
                        ),
                        1.0,
                    ),
                )
                amount = np.where(
                    aggregate_backed_deck,
                    np.maximum(amount, sparse_high_support / 100.0),
                    np.where(upper_only, amount * upper_confidence, amount),
                )
                minimum_member_amount = np.min(
                    np.where(supported_members, target_amount, np.inf), axis=0
                )
                coherent_upper_pair = (
                    (member_count == 2)
                    & (deck_pressure <= _TWO_LEVEL_UPPER_HIGH_MAX_PRESSURE_HPA)
                    & (minimum_member_amount >= 0.20)
                )
                # The same sky patch must be present at both pressure levels;
                # HCC cannot supply the missing overlap. Use the weaker level
                # as the observer-cell support so local point and regional
                # ray-traced paths share one physical cloud footprint.
                supported_amount = minimum_member_amount
                local_upper_support = np.maximum(
                    local_upper_support,
                    np.where(
                        aggregate_backed_deck,
                        sparse_high_support / 100.0,
                        np.where(coherent_upper_pair, supported_amount, 0.0),
                    ),
                )
            deck_amount = np.where(members, amount[None, :], deck_amount)
            family_decks[family_index].append((members, amount))
            family_amount[family_index] = np.clip(
                1.0
                - (1.0 - family_amount[family_index])
                * (1.0 - np.where(present, amount, 0.0)),
                0.0,
                1.0,
            )

    high, mid, low = family_amount
    # HCC is already a union over this high layer. When sparse support was
    # admitted, use that one aggregate-backed representative as the family
    # amount instead of unioning the same weak pressure sample a second time.
    high = np.where(
        sparse_high_support > 0.0,
        sparse_high_support / 100.0,
        high,
    ).astype(np.float32)
    family_amount[0] = high
    # Regional aggregation requires spatial continuity, but an observer cell
    # may still carry a valid sparse upper deck that has no neighbouring GFS
    # cells. Keep that evidence in the native point-only support channel.
    local_upper_support = np.maximum(
        local_upper_support,
        raw_sparse_high_support / 100.0,
    )
    family_geometry_members: list[np.ndarray] = []
    for family_index, decks in enumerate(family_decks):
        geometry_members = np.zeros_like(active)
        if decks:
            dominant_amount = np.maximum.reduce([amount for _, amount in decks])
            for members, amount in decks:
                significant_deck = (
                    np.isclose(amount, dominant_amount)
                    | ((amount >= 0.10) & (amount >= 0.20 * dominant_amount))
                )
                deck_peak = np.max(
                    np.where(members, target_amount, 0.0), axis=0
                )
                significant_layer = (
                    np.isclose(target_amount, deck_peak[None, :])
                    | (
                        (target_amount >= 0.10)
                        & (target_amount >= 0.20 * deck_peak[None, :])
                    )
                )
                significant_members = (
                    members
                    & significant_deck[None, :]
                    & significant_layer
                )
                geometry_members |= significant_members
                deck_opacity = np.max(
                    np.where(significant_members, safe_opacities, 0.0), axis=0
                )
                family_opacity[family_index] = np.clip(
                    1.0
                    - (1.0 - family_opacity[family_index])
                    * (1.0 - deck_opacity),
                    0.0,
                    1.0,
                )
        family_geometry_members.append(geometry_members)
    high_member, mid_member, low_member = family_geometry_members

    def family_wind(family_index: int) -> tuple[np.ndarray, np.ndarray]:
        # Keep every pressure member assigned to this family as a carrier. The
        # cloud amount is the primary weight; a clear target cell falls back to
        # the same family's valid pressure winds so upstream cloud can enter it
        # during the native departure trace.
        family_members = above_ground & (level_families == family_index)
        finite_wind = (
            family_members & np.isfinite(u_winds) & np.isfinite(v_winds)
        )
        cloud_weights = np.where(
            finite_wind & np.isfinite(target_amount) & (target_amount > 0.0),
            target_amount,
            0.0,
        ).astype(np.float64)
        denominator = np.sum(cloud_weights, axis=0)
        condensate_signal = np.clip(
            np.nan_to_num(presence, nan=0.0), 0.0, 1.0
        )
        humidity_signal = np.clip(np.nan_to_num(rhs, nan=0.0) / 100.0, 0.0, 1.0)
        fallback_signal = np.maximum(condensate_signal, humidity_signal)
        fallback_weights = np.where(
            finite_wind,
            np.where(fallback_signal > 0.0, fallback_signal, 1.0),
            0.0,
        ).astype(np.float64)
        fallback_denominator = np.sum(fallback_weights, axis=0)
        weights = np.where(
            denominator[None, :] > 0.0,
            cloud_weights,
            fallback_weights,
        )
        denominator = np.where(
            denominator > 0.0,
            denominator,
            fallback_denominator,
        )
        u_sum = np.sum(
            weights * np.nan_to_num(u_winds, nan=0.0), axis=0, dtype=np.float64
        )
        v_sum = np.sum(
            weights * np.nan_to_num(v_winds, nan=0.0), axis=0, dtype=np.float64
        )
        u = np.divide(
            u_sum,
            denominator,
            out=np.zeros_like(u_sum),
            where=denominator > 0.0,
        )
        v = np.divide(
            v_sum,
            denominator,
            out=np.zeros_like(v_sum),
            where=denominator > 0.0,
        )
        return u.astype(np.float32), v.astype(np.float32)

    high_u, high_v = family_wind(0)
    mid_u, mid_v = family_wind(1)
    low_u, low_v = family_wind(2)

    # Pressure values are samples at level centres. Build the lower interface
    # from the midpoint to the next pressure level, clipped by model terrain.
    # A fixed 1500 m depth at 400 hPa can otherwise extend a valid upper cloud
    # through a high plateau and falsely put the observer inside that cloud.
    layer_bases = np.empty_like(heights, dtype=np.float32)
    for level_index, level in enumerate(levels):
        if level_index + 1 < len(levels):
            lower_center = np.maximum(
                heights[level_index + 1], surface_elevation
            )
            layer_bases[level_index] = 0.5 * (
                heights[level_index] + lower_center
            )
        else:
            layer_bases[level_index] = heights[level_index] - _LEVEL_DEPTH_M[level]
    layer_bases = np.maximum(layer_bases, surface_elevation[None, :])

    boundary_heights = {
        family: _cloud_layer_boundary_heights(
            grid,
            family,
            levels,
            heights,
            temperatures,
            surface_elevation,
        )
        for family in _CLOUD_BOUNDARY_FAMILIES
    }

    def layer_bounds(
        member_mask: np.ndarray,
        family: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        direct_base, direct_top = boundary_heights[family]
        if not np.any(member_mask):
            return (
                np.zeros(grid.latitudes.shape, dtype=np.float32),
                np.zeros(grid.latitudes.shape, dtype=np.float32),
            )
        # Deliberate min/max envelope over significant members only (>=0.10
        # amount or >=0.20x the family peak): two substantial decks keep their
        # combined bounds as independent blocking evidence, while thin
        # companions never enter the mask. Weighted extents were tried and
        # rejected — they broke the retained-bounds contract for substantial
        # decks (test_contiguous_pressure_levels_count_once_as_one_cloud_deck).
        base = np.min(
            np.where(
                member_mask,
                np.maximum(layer_bases, surface_elevation[None, :]),
                np.inf,
            ),
            axis=0,
        )
        top = np.max(np.where(member_mask, heights, 0.0), axis=0)
        direct_valid = (
            np.any(member_mask, axis=0)
            & np.isfinite(direct_base)
            & np.isfinite(direct_top)
            & (direct_top > direct_base)
        )
        base = np.where(direct_valid, np.minimum(base, direct_base), base)
        top = np.where(direct_valid, np.maximum(top, direct_top), top)
        return (
            np.where(np.isfinite(base), base, 0.0).astype(np.float32),
            top.astype(np.float32),
        )

    high_base, high_top = layer_bounds(high_member, "high")
    mid_base, mid_top = layer_bounds(mid_member, "middle")
    low_base, low_top = layer_bounds(low_member, "low")
    high, mid, low = family_amount

    mid_opacity = family_opacity[1]
    low_opacity = family_opacity[2]
    explicit_fields = (
        np.isfinite(fractions) | np.isfinite(liquids) | np.isfinite(ices)
    ) & above_ground
    aggregate_deep_risk = _aggregate_deep_cloud_risk(
        target_amount.T,
        level_values,
        middle_layer_fraction,
        high_layer_fraction,
        (
            boundary_heights["high"],
            boundary_heights["middle"],
            boundary_heights["low"],
        ),
        _field(grid, "surface", 0, "prate"),
    )
    (
        _profile_high,
        _profile_mid,
        _profile_low,
        deep,
        _thickness,
        _cloud_material_class_code,
        column_cap,
        high_material,
        mid_material,
        low_material,
    ) = _vertical_cloud_structure(
        target_amount.T,
        level_values,
        heights.T,
        liquids.T,
        ices.T,
        omegas.T,
        explicit_fields.T,
        aggregate_deep_risk,
    )

    high_material, mid_material, low_material = _apply_connected_deep_material_cap(
        _cloud_material_class_code,
        column_cap,
        high,
        high_base,
        high_top,
        mid,
        mid_base,
        mid_top,
        low,
        low_base,
        low_top,
        high_material,
        mid_material,
        low_material,
    )

    high_body_risk, mid_body_risk, low_body_risk = _resolved_family_body_deep_risks(
        target_amount,
        level_values,
        aggregate_deep_risk,
        (high_member, mid_member, low_member),
        (high, mid, low),
        (high_base, mid_base, low_base),
        (high_top, mid_top, low_top),
    )

    eligible = np.asarray([level <= 700 for level in levels], dtype=bool)[:, None]
    pressure_weight = np.where(
        level_families == 0,
        1.0,
        0.86,
    ).astype(np.float32)
    suitability = np.where(eligible, cloud_amount * pressure_weight * (1.0 - 0.45 * deep[None, :]), -1.0)
    selected = np.argmax(suitability, axis=0)
    cell_indices = np.arange(grid.latitudes.size)
    selected_amount = deck_amount[selected, cell_indices]
    selected_height = heights[selected, cell_indices]
    selected_ice = ices[selected, cell_indices]
    selected_pressure = level_values[selected]
    base = layer_bases[selected, cell_indices]
    output = np.empty((grid.latitudes.size, GRID_FIELD_COUNT), dtype=np.float32)
    output[:, 0] = high
    output[:, 1] = mid
    output[:, 2] = low
    output[:, 3] = deep
    output[:, NATIONAL_GRID_HIGH_MATERIAL] = high_material
    output[:, 5] = base
    output[:, 6] = selected_height
    output[:, 7] = selected_amount
    output[:, 8] = selected_pressure
    output[:, NATIONAL_GRID_HIGH_BODY_RISK] = high_body_risk
    output[:, NATIONAL_GRID_MID_BODY_RISK] = mid_body_risk
    output[:, NATIONAL_GRID_LOW_BODY_RISK] = low_body_risk
    output[:, 12] = selected_ice
    output[:, 13] = _field(grid, "surface", 0, "vis")
    output[:, 14] = _field(grid, "surface", 0, "prate")
    output[:, 15] = mid_opacity
    output[:, 16] = low_opacity
    output[:, 17] = high_base
    output[:, 18] = high_top
    output[:, 19] = mid_base
    output[:, 20] = mid_top
    output[:, 21] = low_base
    output[:, 22] = low_top
    output[:, 23] = family_opacity[0]
    output[:, 24] = surface_elevation
    output[:, NATIONAL_GRID_MID_MATERIAL] = mid_material
    output[:, NATIONAL_GRID_LOW_MATERIAL] = low_material
    output[:, NATIONAL_GRID_HIGH_U] = high_u
    output[:, NATIONAL_GRID_HIGH_V] = high_v
    output[:, NATIONAL_GRID_MID_U] = mid_u
    output[:, NATIONAL_GRID_MID_V] = mid_v
    output[:, NATIONAL_GRID_LOW_U] = low_u
    output[:, NATIONAL_GRID_LOW_V] = low_v
    # Filled from the matching CAMS optical-profile cache immediately before
    # native ray tracing. NaN means no profile evidence, so missing CAMS data
    # cannot invent an aerosol wall from a heuristic AOD.
    output[:, NATIONAL_GRID_AEROSOL_AOD550] = np.nan
    output[:, NATIONAL_GRID_AEROSOL_RESERVED] = np.nan
    output[:, NATIONAL_GRID_LOCAL_UPPER_SUPPORT] = local_upper_support
    return output


def _regional_derivation_worker_count(grid_count: int) -> int:
    configured = os.environ.get("FIRESKY_REGIONAL_DERIVE_WORKERS", "")
    if configured.strip():
        try:
            return max(1, min(grid_count, int(configured)))
        except ValueError as exc:
            raise ValueError(
                "FIRESKY_REGIONAL_DERIVE_WORKERS must be a positive integer"
            ) from exc
    # Each worker temporarily owns the shared pressure stacks.  Eight workers
    # fit the native Apple Silicon deployment's memory budget while allowing
    # the independent forecast hours to use the available cores; callers with
    # tighter memory budgets can still set the environment variable to 1-4.
    return max(1, min(grid_count, os.cpu_count() or 1, 8))


def _derive_grid_tensor_pair(
    grid: CanonicalRegionalGrid,
) -> tuple[np.ndarray, np.ndarray, tuple[int, ...]]:
    try:
        prepared = _prepare_grid_derivation(grid)
    except RuntimeError as exc:
        if str(exc) != "GFS decoded grid has no supported pressure levels":
            raise
        # External adapters may intentionally expose only their validated
        # derivation functions and a terrain/coordinate contract. Preserve
        # that adapter boundary instead of requiring GFS-native pressure keys.
        columns = derive_grid_columns(grid)
        profiles, pressure_levels = derive_grid_layer_profiles(grid)
        return columns, profiles, pressure_levels
    context = getattr(_GRID_DERIVATION_CONTEXT, "values", None)
    if context is None:
        context = {}
        _GRID_DERIVATION_CONTEXT.values = context
    key = id(grid)
    context[key] = (grid, prepared)
    try:
        columns = derive_grid_columns(grid)
        profiles, pressure_levels = derive_grid_layer_profiles(grid)
    finally:
        context.pop(key, None)
    return columns, profiles, pressure_levels


def _derive_grid_tensor_batch(
    grids: Sequence[CanonicalRegionalGrid],
    *,
    model_source: str,
    progress: Callable[[dict[str, Any]], None] | None = None,
    progress_span: int = 12,
) -> list[tuple[np.ndarray, np.ndarray, tuple[int, ...]]]:
    if not grids:
        return []
    worker_count = _regional_derivation_worker_count(len(grids))
    if worker_count == 1:
        derived = [_derive_grid_tensor_pair(grid) for grid in grids]
    else:
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix=f"firesky-{model_source}-derive",
        ) as executor:
            futures = [executor.submit(_derive_grid_tensor_pair, grid) for grid in grids]
            # Consume in forecast-hour order so progress and cache writes keep
            # the same deterministic ordering as the serial implementation.
            derived = []
            for future in futures:
                derived.append(future.result())
                if progress is not None:
                    index = len(derived)
                    progress({
                        "stage": f"tensor_{model_source}",
                        "percent": 20 + int(progress_span * index / len(grids)),
                        "message": f"构建 {model_source.upper()} 云柱张量 {index}/{len(grids)}",
                    })
    if progress is not None and worker_count == 1:
        for index in range(1, len(derived) + 1):
            progress({
                "stage": f"tensor_{model_source}",
                "percent": 20 + int(progress_span * index / len(derived)),
                "message": f"构建 {model_source.upper()} 云柱张量 {index}/{len(derived)}",
            })
    return derived


def _prune_layer_profile_sidecars(directory: Path, keep: Path, limit: int = 4) -> None:
    now = time.time()
    for temporary in directory.glob("*.tmp-*.npy"):
        try:
            if now - temporary.stat().st_mtime > 6 * 3600:
                temporary.unlink(missing_ok=True)
        except OSError:
            continue
    sidecars = sorted(
        (
            path for path in directory.glob("*.npy")
            if (
                path != keep
                and ".tmp-" not in path.name
                and not path.name.endswith(".grid.npy")
                and not path.name.endswith(".surface-cloud.npy")
            )
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in sidecars[max(0, limit - 1):]:
        try:
            if now - stale.stat().st_mtime > 24 * 3600:
                stale_grid = stale.with_name(f"{stale.stem}.grid.npy")
                stale_surface = _model_derived_surface_cloud_path(stale_grid)
                stale_metadata = stale.with_name(f"{stale.stem}.cache-v1.json")
                stale_lock = stale.with_name(f"{stale.stem}.lock")
                with stale_lock.open("a+b") as lock_handle:
                    try:
                        fcntl.flock(
                            lock_handle.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                    except BlockingIOError:
                        continue
                    for path in (stale, stale_grid, stale_surface, stale_metadata):
                        path.unlink(missing_ok=True)
                # Keep the tiny lock inode stable. Removing an flock path can
                # split concurrent waiters between the old and a new inode.
        except OSError:
            continue


_MODEL_DERIVED_CACHE_FORMAT = "firesky-national-model-derived-v1"
_CURRENT_FILE_DIGEST_CACHE_LIMIT = 64
_CURRENT_FILE_DIGEST_CACHE: OrderedDict[tuple[str, int, int], str] = OrderedDict()
_CURRENT_FILE_DIGEST_LOCK = threading.Lock()


def _current_file_sha256(path: Path) -> str:
    """Hash current bytes once per exact path/size/mtime identity."""
    resolved = path.expanduser().resolve()
    while True:
        before = resolved.stat()
        key = (str(resolved), int(before.st_size), int(before.st_mtime_ns))
        with _CURRENT_FILE_DIGEST_LOCK:
            cached = _CURRENT_FILE_DIGEST_CACHE.get(key)
            if cached is not None:
                _CURRENT_FILE_DIGEST_CACHE.move_to_end(key)
                return cached
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        after = resolved.stat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            continue
        value = digest.hexdigest()
        with _CURRENT_FILE_DIGEST_LOCK:
            _CURRENT_FILE_DIGEST_CACHE[key] = value
            _CURRENT_FILE_DIGEST_CACHE.move_to_end(key)
            while len(_CURRENT_FILE_DIGEST_CACHE) > _CURRENT_FILE_DIGEST_CACHE_LIMIT:
                _CURRENT_FILE_DIGEST_CACHE.popitem(last=False)
        return value


def _model_derived_cache_identity(
    paths: Sequence[Path],
    cycle_utc: datetime,
    forecast_hours: Sequence[int],
    bbox: Sequence[float],
    resolution: str,
    model_source: str = "gfs",
    contract: str | None = None,
) -> dict[str, Any]:
    sources = []
    for path in paths:
        resolved = path.expanduser().resolve()
        stat = resolved.stat()
        sidecar = resolved.with_name(resolved.name + ".json")
        sidecar_identity = None
        if sidecar.is_file():
            sidecar_stat = sidecar.stat()
            sidecar_identity = {
                "path": str(sidecar),
                "size_bytes": int(sidecar_stat.st_size),
                "mtime_ns": int(sidecar_stat.st_mtime_ns),
                "sha256": _current_file_sha256(sidecar),
            }
        sources.append({
            "path": str(resolved),
            "size_bytes": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": _current_file_sha256(resolved),
            "sidecar": sidecar_identity,
        })
    source = str(model_source).strip().lower()
    return {
        "format": _MODEL_DERIVED_CACHE_FORMAT,
        "algorithm": NATIONAL_BATCH_ALGORITHM,
        "model_source": source,
        "contract": str(contract or _model_derived_cache_contract(source)),
        "cycle_utc": cycle_utc.astimezone(timezone.utc).isoformat(),
        "forecast_hours": [int(value) for value in forecast_hours],
        "bbox": [float(value) for value in bbox],
        "resolution": str(resolution),
        "sources": sources,
    }


def _model_derived_cache_contract(model_source: str) -> str:
    """Return the strict numeric contract carried by a derived cache."""
    source = str(model_source).strip().lower()
    if source == "ec":
        return "public-open-data/basic"
    if source == "gfs":
        return "gfs-native-pressure-cloud"
    return f"{source}-canonical-pressure-cloud-v1"


def _model_derived_cache_paths(
    first_path: Path,
    forecast_hour_count: int,
    profile_shape: Sequence[int],
    *,
    model_source: str = "gfs",
    bbox: Sequence[float] | None = None,
) -> tuple[Path, Path, Path, Path]:
    digest = _model_derived_cache_digest(
        first_path, model_source=model_source, bbox=bbox
    )
    directory = derived_cache_dir(first_path.resolve(), "national-layer-profiles")
    directory.mkdir(parents=True, exist_ok=True)
    profile_path = directory / (
        f"{digest}-{forecast_hour_count}x{profile_shape[0]}x"
        f"{profile_shape[1]}x{profile_shape[2]}.npy"
    )
    grid_path = profile_path.with_name(f"{profile_path.stem}.grid.npy")
    metadata_path = profile_path.with_name(f"{profile_path.stem}.cache-v1.json")
    lock_path = profile_path.with_name(f"{profile_path.stem}.lock")
    return profile_path, grid_path, metadata_path, lock_path


def _model_derived_surface_cloud_path(grid_path: Path) -> Path:
    """Return the optional exact surface-TCC array beside a derived grid."""
    return grid_path.with_name(f"{grid_path.stem}.surface-cloud.npy")


def _model_derived_cache_digest(
    first_path: Path,
    *,
    model_source: str,
    bbox: Sequence[float] | None,
) -> str:
    bbox_key = "" if bbox is None else "\0".join(
        f"{float(value):.8f}" for value in bbox
    )
    return hashlib.sha256(
        (
            NATIONAL_BATCH_ALGORITHM
            + "\0"
            + str(model_source)
            + "\0"
            + bbox_key
            + "\0"
            + str(first_path)
        ).encode("utf-8")
    ).hexdigest()[:20]


def _load_model_derived_cache(
    profile_path: Path,
    grid_path: Path,
    metadata_path: Path,
    identity: dict[str, Any],
    *,
    hour_count: int,
    cell_count: int | None,
    mmap_grid: bool = False,
) -> tuple[np.ndarray, np.memmap, tuple[int, ...]] | None:
    if not (profile_path.is_file() and grid_path.is_file() and metadata_path.is_file()):
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or metadata.get("identity") != identity:
            return None
        pressure_levels = tuple(int(value) for value in metadata["pressure_levels"])
        grid = np.load(grid_path, mmap_mode="r", allow_pickle=False)
        profiles = np.load(profile_path, mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    if grid.ndim == 3 and cell_count is None:
        cell_count = int(grid.shape[1])
    if (
        cell_count is None
        or
        grid.dtype != np.float32
        or profiles.dtype != np.float32
        or grid.shape != (hour_count, cell_count, GRID_FIELD_COUNT)
        or profiles.shape
        != (hour_count, cell_count, len(pressure_levels), len(LAYER_PROFILE_FIELDS))
        or not pressure_levels
    ):
        return None
    prepared_grid = (
        grid
        if mmap_grid
        else np.array(grid, dtype=np.float32, order="C", copy=True)
    )
    return prepared_grid, profiles, pressure_levels


def _validate_gfs_model_grid_for_national(grid: _DecodedGfsGrid) -> None:
    """Validate the minimum GFS tensor contract before deriving cloud columns."""
    orography = np.asarray(_field(grid, "surface", 0, "orog"), dtype=np.float32)
    if (
        orography.shape != grid.latitudes.shape
        or np.any(~np.isfinite(orography))
        or np.any(orography < -500.0)
        or np.any(orography > 9000.0)
    ):
        raise GfsInputUnavailableError("GFS national input unavailable: invalid surface orography")
    try:
        _validate_canonical_grid_field_ranges(grid)
    except (RuntimeError, ValueError) as exc:
        raise GfsInputUnavailableError(
            f"GFS national input unavailable: {exc}"
        ) from exc
    missing_geometry: list[int] = []
    missing_cloud: list[int] = []
    for level in PRESSURE_LEVELS:
        # A missing pressure surface can be legitimate over high terrain. The
        # standard-atmosphere estimate is only a conservative admission test;
        # it never fills the missing slice or treats it as clear sky.
        pressure_height_m = 44330.0 * (1.0 - (float(level) / 1013.25) ** 0.190263)
        subterranean = orography >= pressure_height_m - 500.0
        height_key = ("isobaricInhPa", level, "gh")
        height = grid.fields.get(height_key)
        if height is None:
            if np.any(~subterranean):
                missing_geometry.append(level)
        elif np.asarray(height).shape != grid.latitudes.shape:
            missing_geometry.append(level)
        elif np.any(~np.isfinite(np.asarray(height, dtype=np.float32)) & ~subterranean):
            missing_geometry.append(level)
        evidence_fields = tuple(
            grid.fields.get(("isobaricInhPa", level, name))
            for name in ("tcc", "tcdc", "clwmr", "icmr")
        )
        evidence_arrays: list[np.ndarray] = []
        for name, value in zip(("tcc", "tcdc", "clwmr", "icmr"), evidence_fields):
            array = (
                np.full(grid.latitudes.shape, np.nan, dtype=np.float32)
                if value is None
                else np.asarray(value, dtype=np.float32)
            )
            if array.shape != grid.latitudes.shape:
                raise GfsInputUnavailableError(
                    f"GFS national input unavailable: invalid cloud field shape {level}/{name}"
                )
            evidence_arrays.append(array)
        amount = np.full(grid.latitudes.shape, np.nan, dtype=np.float32)
        for array in evidence_arrays[:2]:
            amount = np.where(np.isfinite(amount), amount, array)
        liquid, ice = evidence_arrays[2:]
        phase = (
            np.isfinite(liquid)
            & np.isfinite(ice)
            & ((liquid > 0.0) | (ice > 0.0))
        )
        cloud_evidence = np.isfinite(amount) | phase
        if np.any(~cloud_evidence & ~subterranean):
            missing_cloud.append(level)
    if missing_geometry or missing_cloud:
        details: list[str] = []
        if missing_geometry:
            details.append("gh=" + ",".join(str(level) for level in missing_geometry))
        if missing_cloud:
            details.append("cloud=" + ",".join(str(level) for level in missing_cloud))
        raise GfsInputUnavailableError(
            "GFS national input unavailable: required pressure-level fields are "
            + "; ".join(details)
        )


def _external_cache_subset_indices(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    bbox: Sequence[float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Return row-major coordinates and cells for a strict 0.25-degree subset."""
    try:
        requested_bbox = tuple(float(value) for value in bbox)
    except (TypeError, ValueError):
        return None
    if len(requested_bbox) != 4:
        return None
    west, east, north, south = requested_bbox
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        return None
    if not (-90.0 <= south <= north <= 90.0):
        return None
    latitudes = np.asarray(latitudes, dtype=np.float32).reshape(-1)
    longitudes = np.asarray(longitudes, dtype=np.float32).reshape(-1)
    if (
        latitudes.size == 0
        or latitudes.shape != longitudes.shape
        or np.any(~np.isfinite(latitudes))
        or np.any(~np.isfinite(longitudes))
    ):
        return None
    latitude_axis = np.unique(latitudes)
    longitude_axis = np.unique(longitudes)
    if (
        latitude_axis.size * longitude_axis.size != latitudes.size
        or not np.array_equal(latitudes, np.repeat(latitude_axis, longitude_axis.size))
        or not np.array_equal(longitudes, np.tile(longitude_axis, latitude_axis.size))
    ):
        return None
    spacing_values = []
    if latitude_axis.size > 1:
        spacing_values.append(np.diff(latitude_axis))
    if longitude_axis.size > 1:
        spacing_values.append(np.diff(longitude_axis))
    if any(
        np.any(values <= 0.0)
        or not np.allclose(values, 0.25, rtol=0.0, atol=1e-4)
        for values in spacing_values
    ):
        return None
    lat_indices = np.flatnonzero(
        (latitude_axis >= south) & (latitude_axis <= north)
    )
    if west <= east:
        lon_indices = np.flatnonzero(
            (longitude_axis >= west) & (longitude_axis <= east)
        )
    else:
        lon_indices = np.flatnonzero(
            (longitude_axis >= west) | (longitude_axis <= east)
        )
    if lat_indices.size == 0 or lon_indices.size == 0:
        return None
    cell_indices = (
        lat_indices[:, None] * longitude_axis.size + lon_indices[None, :]
    ).reshape(-1)
    return (
        np.repeat(latitude_axis[lat_indices], lon_indices.size),
        np.tile(longitude_axis[lon_indices], lat_indices.size),
        cell_indices,
    )


def _external_bbox_contains(
    outer: Sequence[float], inner: Sequence[float]
) -> bool:
    """Check bbox containment, including antimeridian-wrapping longitudes."""
    try:
        outer_west, outer_east, outer_north, outer_south = (
            float(value) for value in outer
        )
        inner_west, inner_east, inner_north, inner_south = (
            float(value) for value in inner
        )
    except (TypeError, ValueError):
        return False
    if not (
        outer_south <= inner_south
        and outer_north >= inner_north
        and -180.0 <= outer_west <= 180.0
        and -180.0 <= outer_east <= 180.0
        and -180.0 <= inner_west <= 180.0
        and -180.0 <= inner_east <= 180.0
    ):
        return False

    def segments(west: float, east: float) -> tuple[tuple[float, float], ...]:
        return ((west, east),) if west <= east else ((west, 180.0), (-180.0, east))

    outer_segments = segments(outer_west, outer_east)
    return all(
        any(
            outer_left <= inner_left and outer_right >= inner_right
            for outer_left, outer_right in outer_segments
        )
        for inner_left, inner_right in segments(inner_west, inner_east)
    )


def _load_existing_external_derived_cache(
    model_source: str,
    cycle_utc: datetime,
    forecast_hours: Sequence[int],
    bbox: Sequence[float] | None,
    paths: Sequence[Path],
    expected_pressure_levels: Sequence[int] | None = None,
    model_contract: str | None = None,
) -> tuple[np.ndarray, np.memmap, tuple[int, ...], Path] | None:
    """Find an exact or containing external derived cache without decoding values."""

    resolved_paths = [path.expanduser().resolve() for path in paths]
    if not resolved_paths or not all(path.is_file() for path in resolved_paths):
        return None
    source = str(model_source).strip().lower()
    contract = str(model_contract or _model_derived_cache_contract(source)).strip()
    if not contract:
        return None
    identity = _model_derived_cache_identity(
        resolved_paths,
        cycle_utc,
        forecast_hours,
        () if bbox is None else bbox,
        "0p25",
        model_source=source,
        contract=contract,
    )
    directory = derived_cache_dir(resolved_paths[0], "national-layer-profiles")
    if not directory.is_dir():
        return None
    digest = _model_derived_cache_digest(
        resolved_paths[0], model_source=source, bbox=bbox
    )
    prefix = f"{digest}-{len(forecast_hours)}x"

    def load_candidate(
        metadata_path: Path,
        *,
        require_requested_bbox: bool,
    ) -> tuple[np.ndarray, np.memmap, tuple[int, ...], Path] | None:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        candidate_identity = metadata.get("identity") if isinstance(metadata, dict) else None
        if not isinstance(candidate_identity, dict):
            return None
        # The bbox is the only identity component allowed to differ for a
        # superset hit. Raw-file identity, cycle, hours, contract, and
        # algorithm must remain exact.
        expected_without_bbox = dict(identity)
        expected_without_bbox.pop("bbox", None)
        candidate_without_bbox = dict(candidate_identity)
        candidate_bbox = candidate_without_bbox.pop("bbox", None)
        if candidate_without_bbox != expected_without_bbox:
            return None
        if require_requested_bbox:
            if not isinstance(candidate_bbox, list) or len(candidate_bbox) != 4:
                return None
            try:
                candidate_bbox_tuple = tuple(float(value) for value in candidate_bbox)
                requested_bbox = tuple(float(value) for value in bbox or ())
            except (TypeError, ValueError):
                return None
            if len(requested_bbox) != 4 or not _external_bbox_contains(
                candidate_bbox_tuple, requested_bbox
            ):
                return None
        elif candidate_identity.get("bbox") != identity.get("bbox"):
            return None
        base_name = metadata_path.name[: -len(".cache-v1.json")]
        profile_path = directory / f"{base_name}.npy"
        grid_path = directory / f"{base_name}.grid.npy"
        cached = _load_model_derived_cache(
            profile_path,
            grid_path,
            metadata_path,
            candidate_identity,
            hour_count=len(forecast_hours),
            cell_count=None,
            mmap_grid=True,
        )
        if cached is None:
            return None
        if expected_pressure_levels is not None and cached[2] != tuple(
            int(value) for value in expected_pressure_levels
        ):
            return None
        if not require_requested_bbox:
            return (*cached, profile_path)

        coordinates = metadata.get("coordinates")
        if not isinstance(coordinates, dict):
            return None
        try:
            full_latitudes = np.asarray(coordinates["latitudes"], dtype=np.float32)
            full_longitudes = np.asarray(coordinates["longitudes"], dtype=np.float32)
        except (KeyError, TypeError, ValueError):
            return None
        if (
            full_latitudes.size != cached[0].shape[1]
            or full_longitudes.size != full_latitudes.size
        ):
            return None
        subset = _external_cache_subset_indices(
            full_latitudes, full_longitudes, requested_bbox
        )
        if subset is None:
            return None
        latitudes, longitudes, cell_indices = subset
        selected_grid = cached[0][:, cell_indices, :]
        selected_profiles = cached[1][:, cell_indices, :, :]
        if (
            selected_grid.shape[1] != cell_indices.size
            or selected_profiles.shape[1] != cell_indices.size
        ):
            return None
        return selected_grid, selected_profiles, cached[2], profile_path

    for metadata_path in sorted(directory.glob(f"{prefix}*.cache-v1.json")):
        cached = load_candidate(metadata_path, require_requested_bbox=False)
        if cached is not None:
            return cached

    # Prepared regional/download caches use a different bbox in their digest.
    # Scan only metadata after the exact fast path misses; values remain mmap'd
    # and only the requested rectangular cell subset is materialized.
    if bbox is None:
        return None
    for metadata_path in sorted(directory.glob("*.cache-v1.json")):
        if metadata_path.name.startswith(prefix):
            continue
        cached = load_candidate(metadata_path, require_requested_bbox=True)
        if cached is not None:
            return cached
    return None


def _atomic_save_npy(path: Path, values: np.ndarray) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temporary.open("wb") as handle:
            np.save(handle, values, allow_pickle=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_if_bytes_changed(temporary: Path, target: Path) -> None:
    if target.is_file() and target.stat().st_size == temporary.stat().st_size:
        same = True
        with target.open("rb") as current, temporary.open("rb") as candidate:
            while True:
                left = current.read(4 * 1024 * 1024)
                right = candidate.read(4 * 1024 * 1024)
                if left != right:
                    same = False
                    break
                if not left:
                    break
        if same:
            temporary.unlink(missing_ok=True)
            return
    os.replace(temporary, target)


def _load_or_build_external_derived_cache(
    model_source: str,
    cycle_utc: datetime,
    forecast_hours: Sequence[int],
    bbox: Sequence[float] | None,
    grids: Sequence[Any],
    paths: Sequence[Path],
    pressure_levels: tuple[int, ...],
    progress: Callable[[dict[str, Any]], None] | None = None,
    model_contract: str | None = None,
) -> tuple[np.ndarray, np.memmap, tuple[int, ...], Path | None]:
    """Load or build strict derived columns for an external model.

    External model decoders already validate the raw bundle.  This cache only
    stores deterministic column/profile derivation and is invalidated by the
    complete raw-file identity, model, cycle, hours, bbox, and algorithm.
    Offline fixtures with synthetic/nonexistent paths intentionally bypass it.
    """

    source = str(model_source).strip().lower()
    contract = str(model_contract or _model_derived_cache_contract(source)).strip()
    if not contract:
        raise ValueError(f"{source} derived cache contract is empty")
    if not paths or len(paths) != len(grids):
        raise ValueError(f"{source} derived cache inputs are inconsistent")
    if not pressure_levels:
        inferred_pressure_levels = tuple(
            level
            for level in PRESSURE_LEVELS
            if (
                ("isobaricInhPa", level, "gh") in grids[0].fields
                and np.any(
                    np.isfinite(_field(grids[0], "isobaricInhPa", level, "gh"))
                )
            )
        )
        pressure_levels = inferred_pressure_levels
        if not pressure_levels:
            # Keep adapter fixtures and pre-normalized sources that expose
            # their validated pressure contract only through the profile
            # derivation function.
            _, pressure_levels = derive_grid_layer_profiles(grids[0])
    if not all(path.is_file() for path in paths):
        derived = _derive_grid_tensor_batch(
            grids,
            model_source=source,
            progress=progress,
        )
        tensors = [item[0] for item in derived]
        profiles = [item[1] for item in derived]
        return (
            np.ascontiguousarray(np.stack(tensors), dtype=np.float32),
            np.asarray(np.stack(profiles), dtype=np.float32),
            pressure_levels,
            None,
        )

    identity = _model_derived_cache_identity(
        paths,
        cycle_utc,
        forecast_hours,
        () if bbox is None else bbox,
        "0p25",
        model_source=source,
        contract=contract,
    )
    profile_shape = (
        int(grids[0].latitudes.size),
        len(pressure_levels),
        len(LAYER_PROFILE_FIELDS),
    )
    profile_path, grid_path, metadata_path, lock_path = _model_derived_cache_paths(
        paths[0],
        len(forecast_hours),
        profile_shape,
        model_source=source,
        bbox=bbox,
    )
    cached = _load_model_derived_cache(
        profile_path,
        grid_path,
        metadata_path,
        identity,
        hour_count=len(forecast_hours),
        cell_count=int(grids[0].latitudes.size),
    )
    if cached is None:
        with lock_path.open("a+b") as lock_handle:
            acquire_file_lock(
                lock_handle,
                timeout_seconds=300.0,
                description="外部模型派生缓存锁",
            )
            cached = _load_model_derived_cache(
                profile_path,
                grid_path,
                metadata_path,
                identity,
                hour_count=len(forecast_hours),
                cell_count=int(grids[0].latitudes.size),
            )
            if cached is None:
                profile_temporary_path = profile_path.with_name(
                    f".{profile_path.stem}.tmp-{os.getpid()}-{uuid.uuid4().hex}.npy"
                )
                profile_store: np.memmap | None = None
                try:
                    tensors: list[np.ndarray] = []
                    surface_cloud_rows: list[np.ndarray] = []
                    derived = _derive_grid_tensor_batch(
                        grids,
                        model_source=source,
                        progress=progress,
                    )
                    for index, (tensor, profiles, levels) in enumerate(derived, start=1):
                        tensors.append(tensor)
                        grid = grids[index - 1]
                        surface_cloud = np.asarray(
                            grid.fields.get(("surface", 0, "tcc")),
                            dtype=np.float32,
                        ).reshape(-1)
                        if surface_cloud.shape != (int(grids[0].latitudes.size),):
                            surface_cloud = np.full(
                                int(grids[0].latitudes.size), np.nan, dtype=np.float32
                            )
                        surface_cloud_rows.append(surface_cloud)
                        if levels != pressure_levels:
                            raise ValueError(
                                f"{source} source hours do not share one layer profile"
                            )
                        if profile_store is None:
                            profile_store = np.lib.format.open_memmap(
                                profile_temporary_path,
                                mode="w+",
                                dtype=np.float32,
                                shape=(len(forecast_hours),) + profiles.shape,
                            )
                        if profiles.shape != profile_store.shape[1:]:
                            raise ValueError(
                                f"{source} source hours do not share one layer profile shape"
                            )
                        profile_store[index - 1] = profiles
                    if profile_store is None:
                        raise ValueError(f"{source} source returned no profiles")
                    profile_store.flush()
                    _publish_if_bytes_changed(profile_temporary_path, profile_path)
                    _atomic_save_npy(
                        grid_path,
                        np.ascontiguousarray(np.stack(tensors), dtype=np.float32),
                    )
                    _atomic_save_npy(
                        _model_derived_surface_cloud_path(grid_path),
                        np.ascontiguousarray(
                            np.stack(surface_cloud_rows), dtype=np.float32
                        ),
                    )
                    metadata_temporary = metadata_path.with_name(
                        f".{metadata_path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
                    )
                    try:
                        metadata_temporary.write_text(
                            json.dumps(
                                {
                                    "identity": identity,
                                    "pressure_levels": list(pressure_levels),
                                    "coordinates": {
                                        "latitudes": np.asarray(
                                            grids[0].latitudes, dtype=np.float32
                                        ).tolist(),
                                        "longitudes": np.asarray(
                                            grids[0].longitudes, dtype=np.float32
                                        ).tolist(),
                                    },
                                    "surface_cloud_cover": {
                                        "units": "percent",
                                        "path": _model_derived_surface_cloud_path(
                                            grid_path
                                        ).name,
                                    },
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            encoding="utf-8",
                        )
                        os.replace(metadata_temporary, metadata_path)
                    finally:
                        metadata_temporary.unlink(missing_ok=True)
                finally:
                    profile_temporary_path.unlink(missing_ok=True)
                cached = _load_model_derived_cache(
                    profile_path,
                    grid_path,
                    metadata_path,
                    identity,
                    hour_count=len(forecast_hours),
                    cell_count=int(grids[0].latitudes.size),
                )
                if cached is None:
                    raise RuntimeError(f"{source} derived model cache failed strict validation")
                _prune_layer_profile_sidecars(profile_path.parent, profile_path)
    elif progress is not None:
        progress({
            "stage": f"tensor_{source}",
            "percent": 28,
            "message": f"复用 {source.upper()} 严格同身份云柱张量",
        })
    prepared_grid, profile_store, cached_levels = cached
    return prepared_grid, profile_store, cached_levels, profile_path


def prepare_model_columns(
    client,
    cycle_utc: datetime,
    forecast_hours: Sequence[int],
    bbox: tuple[float, float, float, float],
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> NationalBatchPrepared:
    if getattr(client, "model_name", None) != "gfs":
        raise ValueError("national prediction tensor requires a GFS client")
    model_source = "gfs"
    model_label = "GFS"
    ensure_shared_decoded_grid_cache_capacity(max(4, len(forecast_hours)))
    grids: list[_DecodedGfsGrid] = []
    paths: list[str] = []
    first_grid: _DecodedGfsGrid | None = None
    surface_elevation_m: np.ndarray | None = None
    boundary_temporal_semantics = "unknown"
    for hour in forecast_hours:
        grid, path = client.load_decoded_subset(cycle_utc, int(hour), bbox)
        _validate_gfs_model_grid_for_national(grid)
        orography = np.asarray(_field(grid, "surface", 0, "orog"), dtype=np.float32)
        if (
            orography.shape != grid.latitudes.shape
            or np.any(~np.isfinite(orography))
            or np.any(orography < -500.0)
            or np.any(orography > 9000.0)
        ):
            raise RuntimeError(
                f"{model_label} regional grid has missing or invalid surface orography"
            )
        if first_grid is None:
            first_grid = grid
            surface_elevation_m = np.array(orography, dtype=np.float32, copy=True)
            boundary_temporal_semantics = _cloud_boundary_temporal_semantics(grid)
        elif not np.array_equal(first_grid.latitudes, grid.latitudes) or not np.array_equal(first_grid.longitudes, grid.longitudes):
            raise RuntimeError(f"{model_label} regional grids do not share one coordinate layout")
        elif not np.allclose(
            surface_elevation_m,
            orography,
            rtol=0.0,
            atol=MODEL_SURFACE_ELEVATION_MATCH_ATOL_M,
        ):
            raise RuntimeError(
                f"{model_label} regional grids do not share one surface orography"
            )
        grids.append(grid)
        paths.append(str(path))
    if first_grid is None:
        raise RuntimeError(f"regional batch has no {model_label} forecast hours")
    unique_lats = np.unique(first_grid.latitudes)
    unique_lons = np.unique(first_grid.longitudes)
    rows, cols = len(unique_lats), len(unique_lons)
    if rows * cols != first_grid.latitudes.size:
        raise RuntimeError(f"{model_label} regional batch requires a regular rectangular grid")

    inferred_levels = tuple(
        level
        for level in PRESSURE_LEVELS
        if (
            ("isobaricInhPa", level, "gh") in first_grid.fields
            and np.any(np.isfinite(_field(first_grid, "isobaricInhPa", level, "gh")))
        )
    )
    if not inferred_levels:
        raise RuntimeError("GFS decoded grid has no supported pressure levels")
    raw_paths = [Path(value) for value in paths]
    identity = _model_derived_cache_identity(
        raw_paths,
        cycle_utc,
        forecast_hours,
        bbox,
        str(getattr(client, "resolution", "0p25")),
        model_source=str(getattr(client, "model_name", "gfs")),
        contract=_model_derived_cache_contract(str(getattr(client, "model_name", "gfs"))),
    )
    profile_shape = (
        first_grid.latitudes.size,
        len(inferred_levels),
        len(LAYER_PROFILE_FIELDS),
    )
    profile_path, grid_path, metadata_path, lock_path = _model_derived_cache_paths(
        raw_paths[0],
        len(forecast_hours),
        profile_shape,
    )
    cached = _load_model_derived_cache(
        profile_path,
        grid_path,
        metadata_path,
        identity,
        hour_count=len(forecast_hours),
        cell_count=first_grid.latitudes.size,
    )
    if cached is None:
        with lock_path.open("a+b") as lock_handle:
            acquire_file_lock(
                lock_handle,
                timeout_seconds=300.0,
                description="模型派生缓存锁",
            )
            cached = _load_model_derived_cache(
                profile_path,
                grid_path,
                metadata_path,
                identity,
                hour_count=len(forecast_hours),
                cell_count=first_grid.latitudes.size,
            )
            if cached is None:
                tensors: list[np.ndarray] = []
                pressure_levels: tuple[int, ...] = ()
                profile_temporary_path = profile_path.with_name(
                    f".{profile_path.stem}.tmp-{os.getpid()}-{uuid.uuid4().hex}.npy"
                )
                profile_store: np.memmap | None = None
                try:
                    derived = _derive_grid_tensor_batch(
                        grids,
                        model_source=model_source,
                        progress=progress,
                        progress_span=8,
                    )
                    for index, (tensor, profiles, levels) in enumerate(derived, start=1):
                        tensors.append(tensor)
                        if not pressure_levels:
                            pressure_levels = levels
                            profile_store = np.lib.format.open_memmap(
                                profile_temporary_path,
                                mode="w+",
                                dtype=np.float32,
                                shape=(len(forecast_hours),) + profiles.shape,
                            )
                        elif pressure_levels != levels:
                            raise RuntimeError(
                                f"{model_label} regional grids do not share one pressure-level layout"
                            )
                        if profile_store is None or profiles.shape != profile_store.shape[1:]:
                            raise RuntimeError(
                                f"{model_label} regional grids do not share one layer-profile layout"
                            )
                        profile_store[index - 1] = profiles
                    if profile_store is None:
                        raise RuntimeError(f"regional batch has no {model_label} profiles")
                    profile_store.flush()
                    _publish_if_bytes_changed(profile_temporary_path, profile_path)
                    stacked = np.ascontiguousarray(np.stack(tensors), dtype=np.float32)
                    _atomic_save_npy(grid_path, stacked)
                    metadata_temporary = metadata_path.with_name(
                        f".{metadata_path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
                    )
                    try:
                        metadata_temporary.write_text(
                            json.dumps(
                                {
                                    "identity": identity,
                                    "pressure_levels": list(pressure_levels),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            encoding="utf-8",
                        )
                        os.replace(metadata_temporary, metadata_path)
                    finally:
                        metadata_temporary.unlink(missing_ok=True)
                finally:
                    profile_temporary_path.unlink(missing_ok=True)
                cached = _load_model_derived_cache(
                    profile_path,
                    grid_path,
                    metadata_path,
                    identity,
                    hour_count=len(forecast_hours),
                    cell_count=first_grid.latitudes.size,
                )
                if cached is None:
                    raise RuntimeError("GFS derived model cache failed strict validation")
                _prune_layer_profile_sidecars(profile_path.parent, profile_path)
    elif progress is not None:
        progress({
            "stage": f"tensor_{model_source}",
            "percent": 28,
            "message": f"复用 {model_label} 严格同身份云柱张量",
        })
    prepared_grid, profile_store, pressure_levels = cached
    spacing = float(np.median(np.diff(unique_lats))) if rows > 1 else float(np.median(np.diff(unique_lons)))
    return NationalBatchPrepared(
        grid=prepared_grid,
        hours=np.asarray(forecast_hours, dtype=np.float32),
        rows=rows,
        cols=cols,
        lat0=float(unique_lats[0]),
        lon0=float(unique_lons[0]),
        spacing=spacing,
        latitudes=np.asarray(first_grid.latitudes, dtype=np.float32),
        longitudes=np.asarray(first_grid.longitudes, dtype=np.float32),
        cache_paths=paths,
        layer_profiles=profile_store,
        pressure_levels=pressure_levels,
        layer_profile_path=str(profile_path),
        model_source=model_source,
        model_contract=_model_derived_cache_contract(model_source),
        surface_elevation_m=surface_elevation_m,
        boundary_temporal_semantics=boundary_temporal_semantics,
    )


def prepare_regional_model_columns(
    model_source: str,
    cycle_utc: datetime,
    forecast_hours: Sequence[int],
    load_grid: Callable[[datetime, int], tuple[Any, str | Path]],
    *,
    bbox: Sequence[float] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    model_contract: str | None = None,
) -> NationalBatchPrepared:
    """Build shared native tensors from a canonical regional model adapter."""
    source = str(model_source).strip().lower()
    if not source or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", source) is None:
        raise ValueError("regional model source must be a normalized identifier")
    if model_contract is not None and not str(model_contract).strip():
        raise ValueError("regional model contract must not be empty")
    contract = str(model_contract or _model_derived_cache_contract(source)).strip()
    hours = tuple(sorted({int(hour) for hour in forecast_hours}))
    if not hours or any(hour < 0 for hour in hours):
        raise ValueError("external regional model forecast hours are invalid")
    grids: list[Any] = []
    paths: list[str] = []
    latitude_axis: np.ndarray | None = None
    longitude_axis: np.ndarray | None = None
    terrain_reference: np.ndarray | None = None
    pressure_levels: tuple[int, ...] = ()
    for index, hour in enumerate(hours, start=1):
        grid, path = load_grid(cycle_utc, hour)
        latitudes = np.asarray(grid.latitudes, dtype=np.float32)
        longitudes = np.asarray(grid.longitudes, dtype=np.float32)
        unique_lats = np.unique(latitudes)
        unique_lons = np.unique(longitudes)
        if (
            latitudes.ndim != 1
            or longitudes.shape != latitudes.shape
            or unique_lats.size * unique_lons.size != latitudes.size
        ):
            raise ValueError(f"{source} regional grid must be a rectangular coordinate grid")
        if latitude_axis is None:
            latitude_axis, longitude_axis = unique_lats, unique_lons
        elif not (np.array_equal(latitude_axis, unique_lats) and np.array_equal(longitude_axis, unique_lons)):
            raise ValueError(f"{source} source hours do not share one grid layout")
        orography = np.asarray(grid.fields.get(("surface", 0, "orog")), dtype=np.float32)
        if (
            orography.shape != latitudes.shape
            or np.any(~np.isfinite(orography))
            or np.any((orography < -500.0) | (orography > 9000.0))
        ):
            raise ValueError(f"{source} regional grid has invalid surface terrain")
        if terrain_reference is None:
            terrain_reference = np.array(orography, dtype=np.float32, copy=True)
        elif not np.allclose(terrain_reference, orography, rtol=0.0, atol=2.0):
            raise ValueError(f"{source} source hours do not share one terrain field")
        grids.append(grid)
        paths.append(str(Path(path).expanduser().resolve()))
    if latitude_axis is None or longitude_axis is None or terrain_reference is None:
        raise ValueError(f"{source} regional source returned no grids")
    if latitude_axis.size < 2 or longitude_axis.size < 2:
        raise ValueError(f"{source} regional grid must have at least two rows and columns")
    lat_spacing, lon_spacing = np.diff(latitude_axis), np.diff(longitude_axis)
    if not np.allclose(lat_spacing, lat_spacing[0], rtol=0.0, atol=1e-5) or not np.allclose(lon_spacing, lon_spacing[0], rtol=0.0, atol=1e-5):
        raise ValueError(f"{source} regional grid must be regularly spaced")
    if not math.isclose(float(lat_spacing[0]), float(lon_spacing[0]), rel_tol=0.0, abs_tol=1e-4):
        raise ValueError(f"{source} regional grid must use equal latitude/longitude spacing")
    prepared_grid, profile_store, pressure_levels, profile_path = (
        _load_or_build_external_derived_cache(
            source,
            cycle_utc,
            hours,
            bbox,
            grids,
            [Path(path) for path in paths],
            pressure_levels,
            progress,
            contract,
        )
    )
    return NationalBatchPrepared(
        grid=prepared_grid,
        hours=np.asarray(hours, dtype=np.float32), rows=int(latitude_axis.size), cols=int(longitude_axis.size),
        lat0=float(latitude_axis[0]), lon0=float(longitude_axis[0]), spacing=float(lat_spacing[0]),
        latitudes=np.asarray(np.repeat(latitude_axis, longitude_axis.size), dtype=np.float32),
        longitudes=np.asarray(np.tile(longitude_axis, latitude_axis.size), dtype=np.float32),
        cache_paths=paths, layer_profiles=profile_store,
        pressure_levels=pressure_levels,
        layer_profile_path=str(profile_path) if profile_path is not None else None,
        model_source=source,
        model_contract=contract,
        surface_elevation_m=np.ascontiguousarray(terrain_reference, dtype=np.float32),
        boundary_temporal_semantics=(
            "average-envelope-not-instantaneous" if source == "gfs" else "unknown"
        ),
    )


def prepare_external_model_columns(
    model_source: str,
    cycle_utc: datetime,
    forecast_hours: Sequence[int],
    load_grid: Callable[[datetime, int], tuple[Any, str | Path]],
    *,
    bbox: Sequence[float] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> NationalBatchPrepared:
    """Keep the current EC entry point while using the generic adapter path."""
    source = str(model_source).strip().lower()
    if source != "ec":
        raise ValueError("external regional model source must be ec")
    return prepare_regional_model_columns(
        source,
        cycle_utc,
        forecast_hours,
        load_grid,
        bbox=bbox,
        progress=progress,
    )


def _sha256_current_file(path: Path, memo: dict[Path, str]) -> str:
    """Hash current bytes; memoization is scoped to one manifest build only."""
    cached = memo.get(path)
    if cached is not None:
        return cached
    value = _current_file_sha256(path)
    memo[path] = value
    return value


NATIONAL_INPUT_MANIFEST_FORMAT = "firesky-national-input-manifest-v5"
NATIONAL_INPUT_FILE_FIELDS = frozenset({
    "kind",
    "path",
    "size_bytes",
    "mtime_ns",
    "sha256",
    "metadata",
})
NATIONAL_MODEL_INPUT_FILE_FIELDS = frozenset({
    *NATIONAL_INPUT_FILE_FIELDS,
    "forecast_hour",
    "model_source",
    "cycle_utc",
    "resolution",
    "compute_domain_key",
    "source_scope",
    "source_bbox",
})


def _input_file_manifest(
    path_value: str | Path,
    kind: str,
    digest_memo: dict[Path, str],
) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"national input file does not exist: {path}")
    stat = path.stat()
    metadata: dict[str, Any] | None = None
    metadata_path = path.with_name(f"{path.name}.json")
    if metadata_path.is_file():
        try:
            decoded_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            decoded_metadata = None
        if isinstance(decoded_metadata, dict):
            metadata = decoded_metadata
    return {
        "kind": str(kind),
        "path": str(path),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": _sha256_current_file(path, digest_memo),
        "metadata": metadata,
    }


def _validate_national_input_file(
    entry: Any,
    *,
    expected_kind: str | None = None,
    model_file: bool = False,
) -> dict[str, Any]:
    expected_fields = (
        NATIONAL_MODEL_INPUT_FILE_FIELDS if model_file else NATIONAL_INPUT_FILE_FIELDS
    )
    if not isinstance(entry, dict) or set(entry) != expected_fields:
        raise ValueError("national input file entry has invalid fields")
    if (
        not isinstance(entry["kind"], str)
        or not entry["kind"]
        or expected_kind is not None
        and entry["kind"] != expected_kind
    ):
        raise ValueError("national input file kind is invalid")
    if not isinstance(entry["path"], str) or not entry["path"]:
        raise ValueError("national input file path is invalid")
    for field in ("size_bytes", "mtime_ns"):
        value = entry[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"national input file {field} is invalid")
    if entry["size_bytes"] <= 0:
        raise ValueError("national input file size_bytes must be positive")
    if (
        not isinstance(entry["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
    ):
        raise ValueError("national input file sha256 is invalid")
    if entry["metadata"] is not None and not isinstance(entry["metadata"], dict):
        raise ValueError("national input file metadata must be an object or null")
    if model_file:
        if (
            isinstance(entry["forecast_hour"], bool)
            or not isinstance(entry["forecast_hour"], int)
            or entry["forecast_hour"] < 0
        ):
            raise ValueError("national model input forecast_hour is invalid")
        if entry["model_source"] not in {"gfs", "ec"}:
            raise ValueError("national model input source is invalid")
        parse_utc_iso_datetime(
            entry["cycle_utc"], "national model input cycle_utc"
        )
        if entry["resolution"] not in {"0p25", "0p50"}:
            raise ValueError("national model input resolution is invalid")
        if (
            not isinstance(entry["compute_domain_key"], str)
            or not entry["compute_domain_key"]
        ):
            raise ValueError("national model input compute_domain_key is invalid")
        if entry["source_scope"] not in {"regional", "global"}:
            raise ValueError("national model input source_scope is invalid")
        source_bbox = entry["source_bbox"]
        if (
            not isinstance(source_bbox, list)
            or len(source_bbox) != 4
            or any(
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
                for value in source_bbox
            )
        ):
            raise ValueError("national model input source_bbox is invalid")
        west, east, north, south = (float(value) for value in source_bbox)
        if (
            not -180.0 <= west < east <= 180.0
            or not -90.0 <= south < north <= 90.0
        ):
            raise ValueError("national model input source_bbox is invalid")
    return entry


def _validate_model_input_file_provenance(
    entry: dict[str, Any],
    *,
    model_source: str,
    cycle_utc: str,
    compute_bbox: Sequence[Real],
) -> None:
    metadata = entry["metadata"]
    if model_source not in {"gfs", "ec"}:
        raise ValueError("national prediction input provenance has an unsupported source")
    if model_source != "gfs":
        metadata = entry["metadata"]
        expected_metadata_fields = {
            "model", "product", "contract", "cycle", "forecast_hour",
            "grid", "resolution", "algorithm", "aerosol_contract", "file",
        }
        if not isinstance(metadata, dict) or set(metadata) != expected_metadata_fields:
            raise ValueError("external model input sidecar fields are invalid")
        if (
            metadata["model"] != model_source
            or metadata["forecast_hour"] != entry["forecast_hour"]
            or metadata["file"] != Path(entry["path"]).name
            or parse_utc_iso_datetime(metadata["cycle"], "external model input cycle")
            != parse_utc_iso_datetime(cycle_utc, "national model input cycle")
            or metadata["resolution"] != entry["resolution"]
            or metadata["aerosol_contract"] != "cams-vertical-profile-v1"
        ):
            raise ValueError("external model input sidecar identity is inconsistent")
        if not isinstance(metadata["product"], str) or not metadata["product"]:
            raise ValueError("external model input product is invalid")
        if not isinstance(metadata["contract"], str) or not metadata["contract"]:
            raise ValueError("external model input contract is invalid")
        if not isinstance(metadata["grid"], str) or not metadata["grid"]:
            raise ValueError("external model input grid is invalid")
        if not isinstance(metadata["algorithm"], str) or not metadata["algorithm"]:
            raise ValueError("external model input algorithm is invalid")
        if entry["source_scope"] != "regional" or tuple(float(value) for value in entry["source_bbox"]) != tuple(float(value) for value in compute_bbox):
            raise ValueError("external model input source domain is inconsistent")
        return
    base_fields = {"model", "cycle", "forecast_hour", "file", "url", "bbox"}
    is_global = isinstance(metadata, dict) and metadata.get("global") is True
    expected_fields = base_fields | ({"global"} if is_global else set())
    if not isinstance(metadata, dict) or set(metadata) != expected_fields:
        raise ValueError("GFS model input sidecar fields are invalid")
    if (
        metadata["model"] != "gfs"
        or metadata["forecast_hour"] != entry["forecast_hour"]
        or metadata["file"] != Path(entry["path"]).name
        or not isinstance(metadata["url"], str)
        or not metadata["url"]
    ):
        raise ValueError("GFS model input sidecar identity is inconsistent")
    sidecar_cycle = parse_utc_iso_datetime(
        metadata["cycle"], "GFS model input sidecar cycle"
    )
    if sidecar_cycle != parse_utc_iso_datetime(
        cycle_utc, "national model input cycle"
    ):
        raise ValueError("GFS model input sidecar cycle is inconsistent")
    sidecar_bbox = metadata["bbox"]
    if (
        not isinstance(sidecar_bbox, list)
        or len(sidecar_bbox) != 4
        or any(isinstance(value, bool) or not isinstance(value, Real) for value in sidecar_bbox)
        or any(not math.isfinite(float(value)) for value in sidecar_bbox)
    ):
        raise ValueError("GFS model input sidecar bbox is inconsistent")
    source_bbox = tuple(float(value) for value in sidecar_bbox)
    target_bbox = tuple(float(value) for value in compute_bbox)
    expected_scope = "global" if is_global else "regional"
    if entry["source_scope"] != expected_scope or tuple(
        float(value) for value in entry["source_bbox"]
    ) != source_bbox:
        raise ValueError("GFS model input source scope is inconsistent")
    if is_global and source_bbox != (-180.0, 180.0, 90.0, -90.0):
        raise ValueError("GFS global input sidecar bbox is inconsistent")
    if not bbox_contains(source_bbox, target_bbox):
        raise ValueError("GFS model input sidecar bbox does not cover compute domain")
    parsed_url = urlparse(metadata["url"])
    query = parse_qs(parsed_url.query, keep_blank_values=True)
    expected_file = (
        f"gfs.t{sidecar_cycle:%H}z.pgrb2.{entry['resolution']}"
        f".f{entry['forecast_hour']:03d}"
    )
    expected_dir = f"/gfs.{sidecar_cycle:%Y%m%d}/{sidecar_cycle:%H}/atmos"
    expected_filter = f"/cgi-bin/filter_gfs_{entry['resolution']}.pl"
    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc != "nomads.ncep.noaa.gov"
        or parsed_url.path != expected_filter
        or query.get("file") != [expected_file]
        or query.get("dir") != [expected_dir]
    ):
        raise ValueError("GFS model input sidecar URL identity is inconsistent")
    selector_keys = {
        *(f"lev_{level}_mb" for level in PRESSURE_LEVELS),
        "lev_low_cloud_layer",
        "lev_middle_cloud_layer",
        "lev_high_cloud_layer",
        "lev_low_cloud_bottom_level",
        "lev_low_cloud_top_level",
        "lev_middle_cloud_bottom_level",
        "lev_middle_cloud_top_level",
        "lev_high_cloud_bottom_level",
        "lev_high_cloud_top_level",
        "lev_surface",
        *(f"var_{variable}" for variable in VARIABLES),
    }
    spatial_query_keys = {
        "subregion",
        "leftlon",
        "rightlon",
        "toplat",
        "bottomlat",
    }
    expected_query_keys = {"file", "dir", *selector_keys}
    if not is_global:
        expected_query_keys.update(spatial_query_keys)
    if set(query) != expected_query_keys or any(
        query.get(key) != ["on"] for key in selector_keys
    ):
        raise ValueError("GFS model input sidecar URL selectors are inconsistent")
    if not is_global:
        if query.get("subregion") != [""]:
            raise ValueError("GFS model input sidecar URL identity is inconsistent")
        for field, expected in zip(
            ("leftlon", "rightlon", "toplat", "bottomlat"), sidecar_bbox
        ):
            values = query.get(field)
            try:
                actual = (
                    float(values[0])
                    if values is not None and len(values) == 1
                    else math.nan
                )
            except (TypeError, ValueError):
                actual = math.nan
            if not math.isclose(
                actual, float(expected), rel_tol=0.0, abs_tol=1e-9
            ):
                raise ValueError("GFS model input sidecar URL bbox is inconsistent")


def validate_national_input_manifest(manifest: Any) -> dict[str, Any]:
    top_fields = {
        "format",
        "algorithm",
        "model",
        "aerosol",
        "compute_domain",
        "cycle_utc",
        "forecast_hours",
        "terrain",
        "derived",
    }
    if not isinstance(manifest, dict) or set(manifest) != top_fields:
        raise ValueError("national input manifest has invalid fields")
    if manifest["format"] != NATIONAL_INPUT_MANIFEST_FORMAT:
        raise ValueError("national input manifest format is not current")
    if manifest["algorithm"] != NATIONAL_BATCH_ALGORITHM:
        raise ValueError("national input manifest algorithm is not current")
    parse_utc_iso_datetime(manifest["cycle_utc"], "national input manifest cycle_utc")

    model = manifest["model"]
    if not isinstance(model, dict):
        raise ValueError("national input manifest model fields are invalid")
    source = model["source"]
    if source not in {"gfs", "ec"}:
        raise ValueError("national input manifest model source is invalid")
    if source == "gfs":
        expected_model_fields = {"source", "resolution", "files"}
    else:
        expected_model_fields = {"source", "resolution", "contract", "cache_identity", "files"}
    if set(model) != expected_model_fields:
        raise ValueError("national input manifest model fields are invalid")
    if model["resolution"] not in {"0p25", "0p50"}:
        raise ValueError("national input manifest model resolution is invalid")
    forecast_hours = manifest["forecast_hours"]
    if (
        not isinstance(forecast_hours, list)
        or not forecast_hours
        or any(
            isinstance(hour, bool) or not isinstance(hour, int) or hour < 0
            for hour in forecast_hours
        )
        or forecast_hours != sorted(set(forecast_hours))
    ):
        raise ValueError("national input manifest forecast_hours are invalid")
    model_files = model["files"]
    if not isinstance(model_files, list) or len(model_files) != len(forecast_hours):
        raise ValueError("national input manifest model files do not match forecast_hours")
    model_kind = f"{source}-grib2"
    for entry in model_files:
        _validate_national_input_file(
            entry, expected_kind=model_kind, model_file=True
        )
        if (
            entry["model_source"] != source
            or entry["cycle_utc"] != manifest["cycle_utc"]
            or entry["resolution"] != model["resolution"]
        ):
            raise ValueError("national model input identity is inconsistent")
    if source != "gfs":
        if not isinstance(model["contract"], str) or not model["contract"]:
            raise ValueError("external model contract is invalid")
        if not isinstance(model["cache_identity"], str) or not model["cache_identity"]:
            raise ValueError("external model cache identity is invalid")
        try:
            cache_identity = json.loads(model["cache_identity"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("external model cache identity is invalid") from exc
        expected_cache_identity = {
            "source",
            "product",
            "contract",
            "cycle",
            "grid",
            "resolution",
            "algorithm",
            "aerosol_contract",
            "compute_domain_key",
        }
        if (
            not isinstance(cache_identity, dict)
            or set(cache_identity) != expected_cache_identity
            or cache_identity["source"] != source
            or cache_identity["product"] != "ecmwf-ifs-open-data"
            or cache_identity["contract"] != model["contract"]
            or cache_identity["grid"] != "regular_ll_0p25"
            or cache_identity["cycle"] != manifest["cycle_utc"]
            or cache_identity["resolution"] != model["resolution"]
            or cache_identity["algorithm"] != NATIONAL_BATCH_ALGORITHM
            or cache_identity["aerosol_contract"] != "cams-vertical-profile-v1"
            or not isinstance(cache_identity["compute_domain_key"], str)
            or not cache_identity["compute_domain_key"]
        ):
            raise ValueError("external model cache identity is inconsistent")
    if len({entry["path"] for entry in model_files}) != len(model_files):
        raise ValueError("national input manifest model files must be unique")
    if [entry["forecast_hour"] for entry in model_files] != forecast_hours:
        raise ValueError("national model input forecast hours do not match manifest")

    aerosol = manifest["aerosol"]
    if not isinstance(aerosol, dict) or set(aerosol) != {
        "requested_source",
        "actual_sources",
        "files",
        "profile_mode",
        "pressure_levels_hpa",
        "extinction_units",
        "temporal_interpolation",
        "spatial_interpolation",
        "normalization",
    }:
        raise ValueError("national input manifest aerosol fields are invalid")
    if aerosol["requested_source"] not in {"auto", "cams"}:
        raise ValueError("national input manifest requested aerosol source is invalid")
    actual_sources = aerosol["actual_sources"]
    if actual_sources != ["cams"]:
        raise ValueError("national input manifest actual aerosol sources are invalid")
    if not isinstance(aerosol["files"], list) or not aerosol["files"]:
        raise ValueError("national input manifest aerosol files are invalid")
    if aerosol["profile_mode"] != NATIONAL_AEROSOL_PROFILE_MODE:
        raise ValueError("national input manifest aerosol profile mode is invalid")
    if aerosol["pressure_levels_hpa"] != list(
        CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS
    ):
        raise ValueError("national input manifest aerosol pressure levels are invalid")
    if aerosol["extinction_units"] != NATIONAL_AEROSOL_EXTINCTION_UNITS:
        raise ValueError("national input manifest aerosol extinction units are invalid")
    if (
        aerosol["temporal_interpolation"]
        != NATIONAL_AEROSOL_TEMPORAL_INTERPOLATION
    ):
        raise ValueError("national input manifest aerosol time interpolation is invalid")
    if (
        aerosol["spatial_interpolation"]
        != NATIONAL_AEROSOL_SPATIAL_INTERPOLATION
    ):
        raise ValueError("national input manifest aerosol spatial interpolation is invalid")
    if aerosol["normalization"] != NATIONAL_AEROSOL_NORMALIZATION:
        raise ValueError("national input manifest aerosol normalization is invalid")
    for entry in aerosol["files"]:
        _validate_national_input_file(entry, expected_kind="cams-optical-profile")

    compute_domain = manifest["compute_domain"]
    if not isinstance(compute_domain, dict) or set(compute_domain) != {
        "region",
        "bbox",
        "key",
    }:
        raise ValueError("national input manifest compute_domain fields are invalid")
    if compute_domain["region"] is not None and (
        not isinstance(compute_domain["region"], str) or not compute_domain["region"]
    ):
        raise ValueError("national input manifest compute region is invalid")
    bbox = compute_domain["bbox"]
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(isinstance(value, bool) or not isinstance(value, Real) for value in bbox)
        or any(not math.isfinite(float(value)) for value in bbox)
    ):
        raise ValueError("national input manifest compute bbox is invalid")
    west, east, north, south = (float(value) for value in bbox)
    if not -180.0 <= west < east <= 180.0 or not -90.0 <= south < north <= 90.0:
        raise ValueError("national input manifest compute bbox is invalid")
    if not isinstance(compute_domain["key"], str) or not compute_domain["key"]:
        raise ValueError("national input manifest compute domain key is invalid")
    if source != "gfs":
        cache_identity = json.loads(model["cache_identity"])
        if cache_identity["compute_domain_key"] != compute_domain["key"]:
            raise ValueError("external model cache identity domain is inconsistent")
    if any(
        entry["compute_domain_key"] != compute_domain["key"]
        for entry in model_files
    ):
        raise ValueError("national model input compute domain is inconsistent")
    for entry in model_files:
        _validate_model_input_file_provenance(
            entry,
            model_source=source,
            cycle_utc=manifest["cycle_utc"],
            compute_bbox=bbox,
        )

    terrain = manifest["terrain"]
    if not isinstance(terrain, dict) or set(terrain) != {
        "explicit_observer_count",
        "model_grid_observer_count",
        "model_grid_source",
    }:
        raise ValueError("national input manifest terrain fields are invalid")
    for field in ("explicit_observer_count", "model_grid_observer_count"):
        value = terrain[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"national input manifest terrain {field} is invalid")
    if terrain["explicit_observer_count"] + terrain["model_grid_observer_count"] <= 0:
        raise ValueError("national input manifest terrain observer count is empty")
    model_grid_source = terrain["model_grid_source"]
    if terrain["model_grid_observer_count"] == 0:
        if model_grid_source is not None:
            raise ValueError("national input manifest has unexpected model-grid terrain source")
    else:
        if source not in {"gfs", "ec"} or not isinstance(model_grid_source, dict) or set(model_grid_source) != {
            "source",
            "forecast_hour",
            "file",
        }:
            raise ValueError("national input manifest model-grid terrain source is invalid")
        if model_grid_source["source"] not in {"gfs-orography", "ec-orography"}:
            raise ValueError("national input manifest model-grid terrain source is invalid")
        terrain_hour = model_grid_source["forecast_hour"]
        if (
            isinstance(terrain_hour, bool)
            or not isinstance(terrain_hour, int)
            or terrain_hour not in forecast_hours
        ):
            raise ValueError("national input manifest terrain forecast_hour is invalid")
        terrain_file = _validate_national_input_file(
            model_grid_source["file"], expected_kind=f"{source}-grib2", model_file=True
        )
        matching_model_files = [
            entry
            for entry in model_files
            if entry["forecast_hour"] == terrain_hour
        ]
        if len(matching_model_files) != 1 or terrain_file != matching_model_files[0]:
            raise ValueError(
                "national input manifest terrain file is not the matching model file"
            )

    derived = manifest["derived"]
    if not isinstance(derived, dict) or set(derived) != {"files"}:
        raise ValueError("national input manifest derived fields are invalid")
    if not isinstance(derived["files"], list):
        raise ValueError("national input manifest derived files are invalid")
    for entry in derived["files"]:
        _validate_national_input_file(entry, expected_kind="derived-layer-profile")
    return manifest


def build_national_input_manifest(
    prepared: NationalBatchPrepared,
    cams_paths: Sequence[str],
    cycle_utc: datetime,
    forecast_hours: Sequence[int],
    bbox: Sequence[float],
    *,
    model_resolution: str,
    requested_aerosol_source: str,
    actual_aerosol_sources: Sequence[str],
    compute_region: str | None,
    compute_domain_key: str,
    terrain_source: dict[str, Any],
) -> dict[str, Any]:
    """Bind a national result to the exact model and aerosol cache assets."""
    model_source = str(prepared.model_source)
    if model_source not in {"gfs", "ec"}:
        raise ValueError("national input manifest model source is invalid")
    normalized_hours = [int(value) for value in forecast_hours]
    if len(prepared.cache_paths) != len(normalized_hours):
        raise ValueError("national model cache paths do not match forecast hours")
    cycle_value = cycle_utc.isoformat()
    parse_utc_iso_datetime(cycle_value, "national input cycle_utc")
    digest_memo: dict[Path, str] = {}
    model_files = []
    for forecast_hour, path in zip(normalized_hours, prepared.cache_paths):
        entry = _input_file_manifest(path, f"{model_source}-grib2", digest_memo)
        entry.update({
            "forecast_hour": forecast_hour,
            "model_source": model_source,
            "cycle_utc": cycle_value,
            "resolution": str(model_resolution),
            "compute_domain_key": str(compute_domain_key),
        })
        metadata = entry["metadata"]
        if model_source == "gfs":
            if not isinstance(metadata, dict) or not isinstance(metadata.get("bbox"), list):
                raise ValueError("GFS model input requires a current source bbox sidecar")
            entry["source_scope"] = "global" if metadata.get("global") is True else "regional"
            entry["source_bbox"] = [float(value) for value in metadata["bbox"]]
        else:
            # External adapters validate the source artifact before returning it;
            # bind its contract explicitly here even when the upstream cache has
            # no sidecar file.
            existing_artifacts = (
                metadata.get("artifacts")
                if isinstance(metadata, dict)
                else None
            )
            entry["source_scope"] = "regional"
            entry["source_bbox"] = [float(value) for value in bbox]
            entry["metadata"] = {
                "model": model_source,
                "product": "ecmwf-ifs-open-data",
                "contract": "public-open-data/basic",
                "cycle": cycle_value,
                "forecast_hour": forecast_hour,
                "grid": "regular_ll_0p25",
                "resolution": str(model_resolution),
                "algorithm": NATIONAL_BATCH_ALGORITHM,
                "aerosol_contract": "cams-vertical-profile-v1",
                "file": Path(entry["path"]).name,
                **(
                    {}
                ),
            }
        model_files.append(entry)
    cams_files = [
        _input_file_manifest(path, "cams-optical-profile", digest_memo)
        for path in dict.fromkeys(str(value) for value in cams_paths)
    ]
    derived_files: list[dict[str, Any]] = []
    if prepared.layer_profile_path and Path(prepared.layer_profile_path).is_file():
        derived_files.append(
            _input_file_manifest(
                prepared.layer_profile_path,
                "derived-layer-profile",
                digest_memo,
            )
        )
    if not isinstance(terrain_source, dict) or set(terrain_source) != {
        "explicit_observer_count",
        "model_grid_observer_count",
        "model_grid_source",
    }:
        raise ValueError("national terrain provenance is invalid")
    model_grid_source = terrain_source["model_grid_source"]
    terrain_model_file: dict[str, Any] | None = None
    if model_grid_source is not None:
        terrain_hour = model_grid_source["forecast_hour"]
        terrain_path = str(Path(model_grid_source["path"]).expanduser().resolve())
        matching = [
            entry
            for entry in model_files
            if entry["forecast_hour"] == terrain_hour and entry["path"] == terrain_path
        ]
        if len(matching) != 1:
            raise ValueError("national terrain source does not match a model input file")
        terrain_model_file = dict(matching[0])
    terrain_manifest = {
        "explicit_observer_count": terrain_source["explicit_observer_count"],
        "model_grid_observer_count": terrain_source["model_grid_observer_count"],
        "model_grid_source": (
            None
            if model_grid_source is None
            else {
                "source": model_grid_source["source"],
                "forecast_hour": model_grid_source["forecast_hour"],
                "file": terrain_model_file,
            }
        ),
    }
    manifest = {
        "format": NATIONAL_INPUT_MANIFEST_FORMAT,
        "algorithm": NATIONAL_BATCH_ALGORITHM,
        "model": {
            "source": model_source,
            "resolution": str(model_resolution),
            **({
                "contract": "public-open-data/basic",
                "cache_identity": json.dumps(
                    {
                        "source": model_source,
                        "product": "ecmwf-ifs-open-data",
                        "contract": "public-open-data/basic",
                        "cycle": cycle_value,
                        "grid": "regular_ll_0p25",
                        "resolution": str(model_resolution),
                        "algorithm": NATIONAL_BATCH_ALGORITHM,
                        "aerosol_contract": "cams-vertical-profile-v1",
                        "compute_domain_key": str(compute_domain_key),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            } if model_source != "gfs" else {}),
            "files": model_files,
        },
        "aerosol": {
            "requested_source": str(requested_aerosol_source),
            "actual_sources": sorted({
                str(value) for value in actual_aerosol_sources if str(value)
            }),
            "files": cams_files,
            "profile_mode": NATIONAL_AEROSOL_PROFILE_MODE,
            "pressure_levels_hpa": list(CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS),
            "extinction_units": NATIONAL_AEROSOL_EXTINCTION_UNITS,
            "temporal_interpolation": NATIONAL_AEROSOL_TEMPORAL_INTERPOLATION,
            "spatial_interpolation": NATIONAL_AEROSOL_SPATIAL_INTERPOLATION,
            "normalization": NATIONAL_AEROSOL_NORMALIZATION,
        },
        "compute_domain": {
            "region": compute_region,
            "bbox": [float(value) for value in bbox],
            "key": str(compute_domain_key),
        },
        "cycle_utc": cycle_value,
        "forecast_hours": normalized_hours,
        "terrain": terrain_manifest,
        "derived": {"files": derived_files},
    }
    validate_national_input_manifest(manifest)
    return manifest


MODEL_SURFACE_ELEVATION_MATCH_ATOL_M = 1e-4


def validate_prepared_model_surface_elevations(
    prepared: NationalBatchPrepared,
    points: Sequence[dict[str, Any]],
) -> None:
    """Require the prepared first-frame terrain to match the terrain prepass.

    ``surface_elevation_m`` describes the model column used for cloud bases and
    terrain ray blocking. Model-grid observers are populated by the canonical
    terrain prepass before event hours are selected, so changing them here would
    invalidate the already-selected time brackets.
    """
    if not points:
        return
    model_points = [
        point
        for point in points
        if point.get("_elevation_source") == "model-grid"
    ]
    if not model_points:
        return
    if prepared.surface_elevation_m is None:
        raise RuntimeError("最终模式首帧缺少 model-grid 地形场。")
    target_lats = np.asarray([float(point["lat"]) for point in model_points], dtype=np.float64)
    target_lons = np.asarray([float(point["lon"]) for point in model_points], dtype=np.float64)
    elevations = _bilinear_regular_values(
        prepared.latitudes,
        prepared.longitudes,
        prepared.surface_elevation_m,
        target_lats,
        target_lons,
    )
    for point, elevation in zip(model_points, elevations):
        if not np.isfinite(elevation) or not -500.0 <= elevation <= 9000.0:
            raise RuntimeError("最终模式首帧缺少有效的 model-grid 地形高度。")
        prepass_elevation = point.get("elevation_m")
        if (
            isinstance(prepass_elevation, bool)
            or not isinstance(prepass_elevation, (int, float))
            or not np.isfinite(float(prepass_elevation))
        ):
            raise RuntimeError("model-grid 观测点缺少地形预扫高度。")
        if not math.isclose(
            float(prepass_elevation),
            float(elevation),
            rel_tol=0.0,
            abs_tol=MODEL_SURFACE_ELEVATION_MATCH_ATOL_M,
        ):
            raise RuntimeError(
                "模式地形预扫与最终首帧不一致，事件时间 bracket 已失效。"
            )


def apply_decoded_model_surface_elevations(
    grid: _DecodedGfsGrid,
    points: Sequence[dict[str, Any]],
) -> None:
    """Fill model-grid observers from one decoded GFS orography field."""
    model_points = [
        point
        for point in points
        if point.get("_elevation_source") == "model-grid"
    ]
    if not model_points:
        return
    surface_elevation = _field(grid, "surface", 0, "orog")
    target_lats = np.asarray(
        [float(point["lat"]) for point in model_points], dtype=np.float64
    )
    target_lons = np.asarray(
        [float(point["lon"]) for point in model_points], dtype=np.float64
    )
    elevations = _bilinear_regular_values(
        grid.latitudes,
        grid.longitudes,
        surface_elevation,
        target_lats,
        target_lons,
    )
    if np.any(~np.isfinite(elevations)):
        raise RuntimeError("模式地形缺少 model-grid 观测点所需的有效 orography。")
    if np.any((elevations < -500.0) | (elevations > 9000.0)):
        raise RuntimeError("模式地形超出支持的观测者海拔范围。")
    for point, elevation in zip(model_points, elevations):
        point["elevation_m"] = float(elevation)


def _solar_position_from_utc_epoch(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    utc_epoch: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorize Astral's geometric solar position calculation.

    ``prepare_solar_samples`` used to mix a day-of-year NOAA approximation with
    Astral's event time.  Around sunrise/sunset that produced up to a quarter
    degree of elevation drift.  Keep the batch path fast, but use the same
    Julian-century terms, UTC clock, declination and azimuth equations as
    :func:`astral.sun.zenith_and_azimuth` (without atmospheric refraction).
    """
    latitudes = np.asarray(latitudes, dtype=np.float64)
    longitudes = np.asarray(longitudes, dtype=np.float64)
    utc_epoch = np.asarray(utc_epoch, dtype=np.float64)
    if utc_epoch.ndim != 2 or utc_epoch.shape[0] != latitudes.size:
        raise ValueError("utc_epoch must have shape (point_count, step_count)")

    # Astral's julianday() truncates the clock to whole seconds.  Forecast
    # samples are minute-aligned, but preserving that detail keeps the scalar
    # and vectorized paths exactly comparable at event boundaries.
    julian_day = 2440587.5 + np.floor(utc_epoch) / 86400.0
    julian_century = (julian_day - 2451545.0) / 36525.0

    mean_longitude = (
        280.46646
        + julian_century * (36000.76983 + 0.0003032 * julian_century)
    ) % 360.0
    mean_anomaly = 357.52911 + julian_century * (
        35999.05029 - 0.0001537 * julian_century
    )
    anomaly_rad = np.radians(mean_anomaly)
    center = (
        np.sin(anomaly_rad)
        * (1.914602 - julian_century * (0.004817 + 0.000014 * julian_century))
        + np.sin(2.0 * anomaly_rad)
        * (0.019993 - 0.000101 * julian_century)
        + np.sin(3.0 * anomaly_rad) * 0.000289
    )
    true_longitude = mean_longitude + center
    omega = 125.04 - 1934.136 * julian_century
    apparent_longitude = true_longitude - 0.00569 - 0.00478 * np.sin(
        np.radians(omega)
    )
    seconds = 21.448 - julian_century * (
        46.815 + julian_century * (0.00059 - julian_century * 0.001813)
    )
    mean_obliquity = 23.0 + (26.0 + seconds / 60.0) / 60.0
    obliquity = mean_obliquity + 0.00256 * np.cos(np.radians(omega))
    declination = np.degrees(
        np.arcsin(
            np.sin(np.radians(obliquity))
            * np.sin(np.radians(apparent_longitude))
        )
    )

    eccentricity = 0.016708634 - julian_century * (
        0.000042037 + 0.0000001267 * julian_century
    )
    variable_y = np.tan(np.radians(obliquity) / 2.0) ** 2
    longitude_rad = np.radians(mean_longitude)
    equation_of_time = np.degrees(
        variable_y * np.sin(2.0 * longitude_rad)
        - 2.0 * eccentricity * np.sin(anomaly_rad)
        + 4.0
        * eccentricity
        * variable_y
        * np.sin(anomaly_rad)
        * np.cos(2.0 * longitude_rad)
        - 0.5 * variable_y**2 * np.sin(4.0 * longitude_rad)
        - 1.25 * eccentricity**2 * np.sin(2.0 * anomaly_rad)
    ) * 4.0

    utc_minutes = np.mod(np.floor(utc_epoch), 86400.0) / 60.0
    true_solar_time = (
        utc_minutes
        + equation_of_time
        + 4.0 * longitudes[:, None]
    ) % 1440.0
    hour_angle = true_solar_time / 4.0 - 180.0
    latitude_rad = np.radians(np.clip(latitudes, -89.8, 89.8))[:, None]
    declination_rad = np.radians(declination)
    hour_angle_rad = np.radians(hour_angle)
    cos_zenith = (
        np.sin(latitude_rad) * np.sin(declination_rad)
        + np.cos(latitude_rad)
        * np.cos(declination_rad)
        * np.cos(hour_angle_rad)
    )
    zenith_rad = np.arccos(np.clip(cos_zenith, -1.0, 1.0))
    elevation = 90.0 - np.degrees(zenith_rad)

    azimuth_denom = np.cos(latitude_rad) * np.sin(zenith_rad)
    azimuth_ratio = np.divide(
        np.sin(latitude_rad) * np.cos(zenith_rad) - np.sin(declination_rad),
        azimuth_denom,
        out=np.zeros_like(zenith_rad),
        where=np.abs(azimuth_denom) > 0.001,
    )
    azimuth = 180.0 - np.degrees(np.arccos(np.clip(azimuth_ratio, -1.0, 1.0)))
    azimuth = np.where(hour_angle > 0.0, -azimuth, azimuth)
    azimuth = np.mod(azimuth, 360.0)
    polar_fallback = np.abs(azimuth_denom) <= 0.001
    azimuth = np.where(
        polar_fallback,
        np.where(latitudes[:, None] > 0.0, 180.0, 0.0),
        azimuth,
    )
    return elevation, azimuth


def _astral_solar_event_zenith(elevations: np.ndarray) -> np.ndarray:
    # Astral applies the horizon depression for an elevated observer and the
    # standard atmospheric refraction correction at the apparent solar limb.
    elevations = np.asarray(elevations, dtype=np.float64)
    earth_radius_m = 6_356_900.0
    horizon_adjustment = np.zeros_like(elevations)
    elevated = elevations > 0.0
    horizon_adjustment[elevated] = np.degrees(
        np.arccos(earth_radius_m / (earth_radius_m + elevations[elevated]))
    )
    zenith = 90.0 + (16.0 / 60.0) + horizon_adjustment
    apparent_elevation = 90.0 - zenith
    tangent = np.tan(np.radians(apparent_elevation))
    refraction = np.zeros_like(apparent_elevation)
    valid = apparent_elevation < 85.0
    high = valid & (apparent_elevation > 5.0)
    refraction[high] = (
        58.1 / tangent[high]
        - 0.07 / tangent[high] ** 3
        + 0.000086 / tangent[high] ** 5
    ) / 3600.0
    middle = valid & (apparent_elevation <= 5.0) & (apparent_elevation > -0.575)
    step1 = -12.79 + apparent_elevation[middle] * 0.711
    step2 = 103.4 + apparent_elevation[middle] * step1
    step3 = -518.2 + apparent_elevation[middle] * step2
    refraction[middle] = (1735.0 + apparent_elevation[middle] * step3) / 3600.0
    low = valid & (apparent_elevation <= -0.575)
    refraction[low] = -20.774 / tangent[low] / 3600.0
    return zenith + refraction


def _astral_solar_event_utc_minutes(
    target_date: date,
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    elevations: np.ndarray,
    phenomenon: str,
) -> np.ndarray:
    """Vectorized equivalent of Astral's apparent sunrise/sunset transit.

    Point forecasts use :func:`solar_event_time_local`; the national path must
    use the same apparent solar definition or its five-minute cloud samples can
    drift by several minutes.  The two Newton iterations mirror Astral's
    ``time_of_transit`` calculation while keeping the regional path vectorized.
    """
    if phenomenon not in {"dawn", "dusk"}:
        raise ValueError("phenomenon must be dawn or dusk")
    latitudes = np.asarray(latitudes, dtype=np.float64)
    longitudes = np.asarray(longitudes, dtype=np.float64)
    elevations = np.asarray(elevations, dtype=np.float64)
    if not (latitudes.shape == longitudes.shape == elevations.shape):
        raise ValueError("solar event arrays must have matching shapes")

    zenith = _astral_solar_event_zenith(elevations)

    year = target_date.year
    month = target_date.month
    day = target_date.day
    if month <= 2:
        year -= 1
        month += 12
    century = int(year / 100)
    julian_day = (
        int(365.25 * (year + 4716))
        + int(30.6001 * (month + 1))
        + day
        + (2 - century + int(century / 4))
        - 1524.5
    )
    adjustment = np.zeros_like(latitudes)
    clipped_latitudes = np.clip(latitudes, -89.8, 89.8)
    latitude_radians = np.radians(clipped_latitudes)
    for _ in range(2):
        julian_century = (julian_day + adjustment - 2451545.0) / 36525.0
        mean_longitude = (
            280.46646
            + julian_century * (36000.76983 + 0.0003032 * julian_century)
        ) % 360.0
        mean_anomaly = 357.52911 + julian_century * (
            35999.05029 - 0.0001537 * julian_century
        )
        anomaly_radians = np.radians(mean_anomaly)
        center = (
            np.sin(anomaly_radians)
            * (1.914602 - julian_century * (0.004817 + 0.000014 * julian_century))
            + np.sin(2.0 * anomaly_radians)
            * (0.019993 - 0.000101 * julian_century)
            + np.sin(3.0 * anomaly_radians) * 0.000289
        )
        true_longitude = mean_longitude + center
        omega = 125.04 - 1934.136 * julian_century
        apparent_longitude = true_longitude - 0.00569 - 0.00478 * np.sin(
            np.radians(omega)
        )
        seconds = 21.448 - julian_century * (
            46.815 + julian_century * (0.00059 - julian_century * 0.001813)
        )
        mean_obliquity = 23.0 + (26.0 + seconds / 60.0) / 60.0
        obliquity = mean_obliquity + 0.00256 * np.cos(np.radians(omega))
        declination = np.degrees(
            np.arcsin(
                np.sin(np.radians(obliquity))
                * np.sin(np.radians(apparent_longitude))
            )
        )
        eccentricity = 0.016708634 - julian_century * (
            0.000042037 + 0.0000001267 * julian_century
        )
        variable_y = np.tan(np.radians(obliquity) / 2.0) ** 2
        longitude_radians = np.radians(mean_longitude)
        anomaly_sine = np.sin(anomaly_radians)
        equation_of_time = np.degrees(
            variable_y * np.sin(2.0 * longitude_radians)
            - 2.0 * eccentricity * anomaly_sine
            + 4.0
            * eccentricity
            * variable_y
            * anomaly_sine
            * np.cos(2.0 * longitude_radians)
            - 0.5 * variable_y**2 * np.sin(4.0 * longitude_radians)
            - 1.25 * eccentricity**2 * np.sin(2.0 * anomaly_radians)
        ) * 4.0
        cosine_hour_angle = (
            np.cos(np.radians(zenith))
            - np.sin(latitude_radians) * np.sin(np.radians(declination))
        ) / (
            np.cos(latitude_radians) * np.cos(np.radians(declination))
        )
        hour_angle = np.degrees(np.arccos(np.clip(cosine_hour_angle, -1.0, 1.0)))
        if phenomenon == "dusk":
            hour_angle = -hour_angle
        offset = (-longitudes - hour_angle) * 4.0 - equation_of_time
        offset = np.where(offset < -720.0, offset + 1440.0, offset)
        utc_minutes = 720.0 + offset
        adjustment = utc_minutes / 1440.0
    return utc_minutes


def _solar_event_epoch_on_local_date(
    target_date: date,
    latitude: float,
    longitude: float,
    elevation_m: float,
    timezone_name: str,
    phenomenon: str,
    approximate_epoch: float,
) -> float:
    """Resolve an Astral-equivalent transit on the requested local date."""
    tzinfo = ZoneInfo(timezone_name)
    local_start = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        tzinfo=tzinfo,
    )
    local_end_date = target_date + timedelta(days=1)
    local_end = datetime(
        local_end_date.year,
        local_end_date.month,
        local_end_date.day,
        tzinfo=tzinfo,
    )
    approximate_local = datetime.fromtimestamp(approximate_epoch, tz=timezone.utc).astimezone(
        tzinfo
    )
    approximate_local += timedelta(days=(target_date - approximate_local.date()).days)
    seed = approximate_local.timestamp()
    target_elevation = 90.0 - float(
        _astral_solar_event_zenith(np.asarray([elevation_m], dtype=np.float64))[0]
    )

    def residual(epoch: float) -> float:
        solar_elevation, _ = _solar_position_from_utc_epoch(
            np.asarray([latitude], dtype=np.float64),
            np.asarray([longitude], dtype=np.float64),
            np.asarray([[epoch]], dtype=np.float64),
        )
        return float(solar_elevation[0, 0]) - target_elevation

    half_window_seconds = 6.0 * 3600.0
    lower = max(local_start.timestamp(), seed - half_window_seconds)
    upper = min(local_end.timestamp(), seed + half_window_seconds)
    lower_value = residual(lower)
    upper_value = residual(upper)
    bracketed = (
        lower_value <= 0.0 <= upper_value
        if phenomenon == "dawn"
        else upper_value <= 0.0 <= lower_value
    )
    if not bracketed:
        raise ValueError(
            f"Unable to find a {phenomenon} time on the requested local date"
        )
    for _ in range(48):
        middle = 0.5 * (lower + upper)
        middle_value = residual(middle)
        if (phenomenon == "dawn" and middle_value < 0.0) or (
            phenomenon == "dusk" and middle_value > 0.0
        ):
            lower = middle
        else:
            upper = middle
    return 0.5 * (lower + upper)


def prepare_solar_event_timeline(
    points: Sequence[dict[str, Any]],
    target_date: date,
    phenomenon: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    point_count = len(points)
    offsets = np.asarray(national_event_offsets_minutes(phenomenon), dtype=np.float64)
    latitudes = np.asarray([point["lat"] for point in points], dtype=np.float64)
    longitudes = np.asarray([point["lon"] for point in points], dtype=np.float64)
    elevation_values: list[float] = []
    for point in points:
        raw_elevation = point["elevation_m"]
        if isinstance(raw_elevation, bool) or not isinstance(raw_elevation, Real):
            raise ValueError("solar event point elevation_m must be a finite number")
        elevation = float(raw_elevation)
        if not math.isfinite(elevation) or not -500.0 <= elevation <= 9000.0:
            raise ValueError(
                "solar event point elevation_m must be between -500 and 9000 metres"
            )
        elevation_values.append(elevation)
    elevations = np.asarray(elevation_values, dtype=np.float32)
    timezone_offsets = np.asarray([
        datetime(target_date.year, target_date.month, target_date.day, 12, tzinfo=ZoneInfo(str(point["timezone_name"]))).utcoffset().total_seconds() / 3600.0
        for point in points
    ], dtype=np.float64)
    initial_utc_event_minutes = _astral_solar_event_utc_minutes(
        target_date,
        latitudes,
        longitudes,
        elevations,
        phenomenon,
    )
    local_midnight_epoch = np.asarray([
        datetime(target_date.year, target_date.month, target_date.day, tzinfo=ZoneInfo(str(point["timezone_name"]))).timestamp()
        for point in points
    ], dtype=np.float64)
    local_day_delta = np.floor(
        (initial_utc_event_minutes + timezone_offsets * 60.0) / 1440.0
    ).astype(np.int64)
    utc_event_minutes = initial_utc_event_minutes.copy()
    target_utc_midnight_epoch = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        tzinfo=timezone.utc,
    ).timestamp()
    utc_midnight_epoch = np.full(
        point_count, target_utc_midnight_epoch, dtype=np.float64
    )
    # Astral retries the adjacent UTC date when the first transit lands on a
    # different local calendar date (notably sunrise in east-Asian time zones).
    for delta in np.unique(local_day_delta[local_day_delta != 0]):
        mask = local_day_delta == delta
        transit_date = target_date - timedelta(days=int(delta))
        utc_event_minutes[mask] = _astral_solar_event_utc_minutes(
            transit_date,
            latitudes[mask],
            longitudes[mask],
            elevations[mask],
            phenomenon,
        )
        utc_midnight_epoch[mask] = datetime(
            transit_date.year,
            transit_date.month,
            transit_date.day,
            tzinfo=timezone.utc,
        ).timestamp()
    anchor_epoch = utc_midnight_epoch + utc_event_minutes * 60.0
    for point_index, point in enumerate(points):
        timezone_name = str(point["timezone_name"])
        anchor_local_date = datetime.fromtimestamp(
            float(anchor_epoch[point_index]), tz=timezone.utc
        ).astimezone(ZoneInfo(timezone_name)).date()
        if anchor_local_date != target_date:
            anchor_epoch[point_index] = _solar_event_epoch_on_local_date(
                target_date,
                float(latitudes[point_index]),
                float(longitudes[point_index]),
                float(elevations[point_index]),
                timezone_name,
                phenomenon,
                target_utc_midnight_epoch
                + float(initial_utc_event_minutes[point_index]) * 60.0,
            )
    anchor_minutes = (anchor_epoch - local_midnight_epoch) / 60.0
    local_minutes = anchor_minutes[:, None] + offsets[None, :]
    evaluation_epoch = local_midnight_epoch[:, None] + local_minutes * 60.0
    elevation, azimuth = _solar_position_from_utc_epoch(
        latitudes,
        longitudes,
        evaluation_epoch,
    )
    valid = (elevation >= -7.5) & (elevation <= 1.5)
    anchor_index = int(np.argmin(np.abs(offsets)))
    valid[:, anchor_index] = True

    observers = np.column_stack((latitudes, longitudes, elevations)).astype(np.float32)
    return observers, elevation, azimuth, valid, evaluation_epoch, anchor_epoch


def prepare_solar_samples(
    points: Sequence[dict[str, Any]],
    target_date: date,
    phenomenon: str,
    cycle_utc: datetime,
    hours: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    (
        observers,
        elevation,
        azimuth,
        valid,
        evaluation_epoch,
        anchor_epoch,
    ) = prepare_solar_event_timeline(points, target_date, phenomenon)
    hours = np.asarray(hours, dtype=np.float64)
    if (
        hours.ndim != 1
        or hours.size == 0
        or np.any(~np.isfinite(hours))
        or np.any(np.diff(hours) <= 0.0)
    ):
        raise ValueError("模式预报小时必须是非空、有限且严格递增的一维数组。")

    cycle_epoch = cycle_utc.timestamp()
    forecast_offset = (evaluation_epoch - cycle_epoch) / 3600.0
    insertion = np.searchsorted(hours, forecast_offset, side="left")
    candidate_in_range = insertion < hours.size
    exact = np.zeros(forecast_offset.shape, dtype=bool)
    exact[candidate_in_range] = np.isclose(
        hours[insertion[candidate_in_range]],
        forecast_offset[candidate_in_range],
        rtol=0.0,
        atol=1e-7,
    )
    between = (insertion > 0) & (insertion < hours.size) & ~exact
    bracketed = exact | between
    missing = valid & ~bracketed
    if np.any(missing):
        missing_offsets = forecast_offset[missing]
        raise RuntimeError(
            "模式时间轴未覆盖有效太阳样本："
            f"需要 {float(np.min(missing_offsets)):.6f}.."
            f"{float(np.max(missing_offsets)):.6f}h，"
            f"现有 {float(hours[0]):.6f}..{float(hours[-1]):.6f}h。"
        )
    left = np.zeros(forecast_offset.shape, dtype=np.intp)
    right = np.zeros(forecast_offset.shape, dtype=np.intp)
    left[exact] = insertion[exact]
    right[exact] = insertion[exact]
    left[between] = insertion[between] - 1
    right[between] = insertion[between]
    weight = np.zeros(forecast_offset.shape, dtype=np.float64)
    denominator = hours[right[between]] - hours[left[between]]
    weight[between] = (
        forecast_offset[between] - hours[left[between]]
    ) / denominator
    if np.any((weight[between] < -1e-7) | (weight[between] > 1.0 + 1e-7)):
        raise RuntimeError("模式时间插值权重超出有效范围。")
    left_seconds = np.where(
        valid & bracketed,
        np.maximum((forecast_offset - hours[left]) * 3600.0, 0.0),
        0.0,
    )
    right_seconds = np.where(
        valid & bracketed,
        np.maximum((hours[right] - forecast_offset) * 3600.0, 0.0),
        0.0,
    )

    samples = np.empty(
        (len(points), evaluation_epoch.shape[1], NATIONAL_SAMPLE_FIELDS), dtype=np.float32
    )
    samples[:, :, 0] = elevation
    samples[:, :, 1] = azimuth
    samples[:, :, 2] = left
    samples[:, :, 3] = right
    samples[:, :, 4] = weight
    samples[:, :, NATIONAL_SAMPLE_LOCAL_AOD] = 0.12
    samples[:, :, NATIONAL_SAMPLE_VALID] = valid
    samples[:, :, NATIONAL_SAMPLE_LEFT_SECONDS] = left_seconds
    samples[:, :, NATIONAL_SAMPLE_RIGHT_SECONDS] = right_seconds
    return observers, samples, evaluation_epoch, anchor_epoch


def _nearest_regular_values(
    grid_lats: np.ndarray,
    grid_lons: np.ndarray,
    values: np.ndarray,
    target_lats: np.ndarray,
    target_lons: np.ndarray,
) -> np.ndarray:
    normalized_lons = (np.asarray(grid_lons) + 180.0) % 360.0 - 180.0
    unique_lats = np.unique(grid_lats)
    unique_lons = np.unique(normalized_lons)
    matrix = np.full((len(unique_lats), len(unique_lons)), np.nan, dtype=np.float32)
    lat_index = np.searchsorted(unique_lats, grid_lats)
    lon_index = np.searchsorted(unique_lons, normalized_lons)
    matrix[lat_index, lon_index] = values

    def nearest(axis: np.ndarray, targets: np.ndarray) -> np.ndarray:
        upper = np.clip(np.searchsorted(axis, targets), 0, len(axis) - 1)
        lower = np.maximum(0, upper - 1)
        return np.where(np.abs(axis[lower] - targets) <= np.abs(axis[upper] - targets), lower, upper)

    return matrix[nearest(unique_lats, target_lats), nearest(unique_lons, target_lons)]


def _prepare_regular_grid_sampler(
    grid_lats: np.ndarray,
    grid_lons: np.ndarray,
    values: np.ndarray,
) -> _RegularGridSampler:
    unique_lats, unique_lons, lat_indices, lon_indices = _regular_grid_axes(
        grid_lats, grid_lons
    )
    matrix = np.full((len(unique_lats), len(unique_lons)), np.nan, dtype=np.float64)
    matrix[lat_indices, lon_indices] = np.asarray(values, dtype=np.float64)
    return _RegularGridSampler(unique_lats, unique_lons, matrix)


def _prepare_regular_grid_stencil(
    sampler: _RegularGridSampler,
    target_lats: np.ndarray,
    target_lons: np.ndarray,
) -> _RegularGridStencil:
    target_lats = np.asarray(target_lats, dtype=np.float64)
    target_lons = (np.asarray(target_lons, dtype=np.float64) + 180.0) % 360.0 - 180.0

    def bounds(axis: np.ndarray, targets: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        upper = np.clip(np.searchsorted(axis, targets), 0, len(axis) - 1)
        lower = np.maximum(0, upper - 1)
        denominator = axis[upper] - axis[lower]
        weight = np.zeros_like(targets, dtype=np.float64)
        np.divide(targets - axis[lower], denominator, out=weight, where=denominator > 0.0)
        return lower, upper, np.clip(weight, 0.0, 1.0)

    lat0, lat1, lat_weight = bounds(sampler.latitudes, target_lats)
    lon0, lon1, lon_weight = bounds(sampler.longitudes, target_lons)
    return _RegularGridStencil(
        lat0=lat0,
        lat1=lat1,
        lat_weight=lat_weight,
        lon0=lon0,
        lon1=lon1,
        lon_weight=lon_weight,
        nearest_lat=np.where(lat_weight <= 0.5, lat0, lat1),
        nearest_lon=np.where(lon_weight <= 0.5, lon0, lon1),
        inside=(
            (target_lats >= sampler.latitudes[0])
            & (target_lats <= sampler.latitudes[-1])
            & (target_lons >= sampler.longitudes[0])
            & (target_lons <= sampler.longitudes[-1])
        ),
    )


def _bilinear_sampler_values_with_stencil(
    sampler: _RegularGridSampler,
    stencil: _RegularGridStencil,
) -> np.ndarray:
    q00 = sampler.matrix[stencil.lat0, stencil.lon0]
    q01 = sampler.matrix[stencil.lat0, stencil.lon1]
    q10 = sampler.matrix[stencil.lat1, stencil.lon0]
    q11 = sampler.matrix[stencil.lat1, stencil.lon1]
    result = (
        q00 * (1.0 - stencil.lat_weight) * (1.0 - stencil.lon_weight)
        + q01 * (1.0 - stencil.lat_weight) * stencil.lon_weight
        + q10 * stencil.lat_weight * (1.0 - stencil.lon_weight)
        + q11 * stencil.lat_weight * stencil.lon_weight
    )
    fallback = sampler.matrix[stencil.nearest_lat, stencil.nearest_lon]
    return np.where(np.isfinite(result), result, fallback)


def _bilinear_sampler_values(
    sampler: _RegularGridSampler,
    target_lats: np.ndarray,
    target_lons: np.ndarray,
) -> np.ndarray:
    def bounds(axis: np.ndarray, targets: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        upper = np.clip(np.searchsorted(axis, targets), 0, len(axis) - 1)
        lower = np.maximum(0, upper - 1)
        denominator = axis[upper] - axis[lower]
        weight = np.zeros_like(targets, dtype=np.float64)
        np.divide(targets - axis[lower], denominator, out=weight, where=denominator > 0.0)
        return lower, upper, np.clip(weight, 0.0, 1.0)

    target_lats = np.asarray(target_lats, dtype=np.float64)
    target_lons = (np.asarray(target_lons, dtype=np.float64) + 180.0) % 360.0 - 180.0
    lat0, lat1, lat_weight = bounds(sampler.latitudes, target_lats)
    lon0, lon1, lon_weight = bounds(sampler.longitudes, target_lons)
    q00 = sampler.matrix[lat0, lon0]
    q01 = sampler.matrix[lat0, lon1]
    q10 = sampler.matrix[lat1, lon0]
    q11 = sampler.matrix[lat1, lon1]
    result = (
        q00 * (1.0 - lat_weight) * (1.0 - lon_weight)
        + q01 * (1.0 - lat_weight) * lon_weight
        + q10 * lat_weight * (1.0 - lon_weight)
        + q11 * lat_weight * lon_weight
    )
    nearest_lat = np.where(lat_weight <= 0.5, lat0, lat1)
    nearest_lon = np.where(lon_weight <= 0.5, lon0, lon1)
    fallback = sampler.matrix[nearest_lat, nearest_lon]
    return np.where(np.isfinite(result), result, fallback)


def _bilinear_sampler_values_with_coverage(
    sampler: _RegularGridSampler,
    target_lats: np.ndarray,
    target_lons: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    target_lats = np.asarray(target_lats, dtype=np.float64)
    target_lons = (np.asarray(target_lons, dtype=np.float64) + 180.0) % 360.0 - 180.0
    inside = (
        (target_lats >= sampler.latitudes[0])
        & (target_lats <= sampler.latitudes[-1])
        & (target_lons >= sampler.longitudes[0])
        & (target_lons <= sampler.longitudes[-1])
    )
    values = _bilinear_sampler_values(sampler, target_lats, target_lons)
    return values, inside & np.isfinite(values)


def _bilinear_regular_values(
    grid_lats: np.ndarray,
    grid_lons: np.ndarray,
    values: np.ndarray,
    target_lats: np.ndarray,
    target_lons: np.ndarray,
) -> np.ndarray:
    return _bilinear_sampler_values(
        _prepare_regular_grid_sampler(grid_lats, grid_lons, values),
        target_lats,
        target_lons,
    )


def _map_cams_optics_to_model_grid(
    optics,
    target_lats: np.ndarray,
    target_lons: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Map one strict CAMS column tensor to the regular model grid."""
    channels = np.concatenate(
        (optics.aod550[None, :], optics.aerext532_m_inv), axis=0
    )
    mapped = np.empty((channels.shape[0], target_lats.size), dtype=np.float32)
    covered = np.ones(target_lats.shape, dtype=bool)
    unique_lats, unique_lons, lat_indices, lon_indices = _regular_grid_axes(
        optics.lats, optics.lons
    )
    stencil = _prepare_regular_grid_stencil(
        _RegularGridSampler(
            unique_lats,
            unique_lons,
            np.empty((len(unique_lats), len(unique_lons)), dtype=np.float64),
        ),
        target_lats,
        target_lons,
    )
    for channel_index, values in enumerate(channels):
        matrix = np.full((len(unique_lats), len(unique_lons)), np.nan, dtype=np.float64)
        matrix[lat_indices, lon_indices] = np.asarray(values, dtype=np.float64)
        channel = _bilinear_sampler_values_with_stencil(
            _RegularGridSampler(unique_lats, unique_lons, matrix), stencil
        )
        channel_covered = stencil.inside & np.isfinite(channel)
        mapped[channel_index] = channel
        covered &= channel_covered & np.isfinite(channel)
    covered &= (
        (mapped[0] >= 0.0)
        & (mapped[0] <= 10.0)
        & np.all(mapped[1:] >= 0.0, axis=0)
    )
    return mapped[0], np.ascontiguousarray(mapped[1:].T), covered


def _apply_cams_aerosol_profiles(
    prepared: NationalBatchPrepared,
    samples: np.ndarray,
    observers: np.ndarray,
    anchor_epoch: np.ndarray,
    cycle_utc: datetime,
    cams_cache_dir: Path,
    cams_area: list[float] | None,
) -> tuple[np.ndarray, list[str], list[str], list[str], float]:
    """Build the one AOD/profile tensor used by point and regional scoring."""
    samples = np.asarray(samples)
    if samples.ndim != 3 or samples.shape[2] != NATIONAL_SAMPLE_FIELDS:
        raise ValueError(
            f"national samples must have {NATIONAL_SAMPLE_FIELDS} fields"
        )
    if prepared.grid.shape[-1] != GRID_FIELD_COUNT:
        raise ValueError(
            f"national grid must have {GRID_FIELD_COUNT} fields, got "
            f"{prepared.grid.shape[-1]}"
        )
    observers = np.asarray(observers)
    if observers.shape != (samples.shape[0], 3):
        raise ValueError("national observers must match the aerosol sample points")
    prepared.grid[:, :, NATIONAL_GRID_AEROSOL_AOD550] = 0.0
    prepared.grid[:, :, NATIONAL_GRID_AEROSOL_RESERVED] = np.nan
    aerosol_profiles = np.zeros(
        (
            prepared.grid.shape[0],
            prepared.grid.shape[1],
            CAMS_AEROSOL_PROFILE_COUNT,
        ),
        dtype=np.float32,
    )
    point_sources = [""] * len(observers)
    point_qualities = ["cams"] * len(observers)
    valid = samples[:, :, NATIONAL_SAMPLE_VALID] >= 0.5
    if not np.any(valid) or not cams_area:
        raise RuntimeError("National scoring requires a complete CAMS profile area")

    referenced = np.concatenate((samples[:, :, 2][valid], samples[:, :, 3][valid]))
    time_indices = np.unique(np.rint(referenced).astype(np.intp))
    if np.any((time_indices < 0) | (time_indices >= len(prepared.hours))):
        raise ValueError("solar samples reference a forecast time outside the grid")

    mapped_cache: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    source_paths: list[str] = []
    covered_cells = 0
    requested_cells = 0
    target_lats = np.asarray(prepared.latitudes, dtype=np.float64)
    target_lons = np.asarray(prepared.longitudes, dtype=np.float64)
    for time_index in time_indices:
        valid_time = cycle_utc + timedelta(hours=float(prepared.hours[time_index]))
        selection = find_cached_cams_bracket(cams_cache_dir, cams_area, valid_time)
        requested_cells += target_lats.size
        if selection is None:
            raise RuntimeError(
                f"No cached CAMS optical profile covers {valid_time:%Y-%m-%d %H:%MZ}."
            )

        bracket_values = []
        for file_selection in (selection.left, selection.right):
            path_key = str(file_selection.path.resolve())
            mapped = mapped_cache.get(path_key)
            if mapped is None:
                optics = load_cams_optics_grid(file_selection.path)
                if optics.valid_time != file_selection.valid_time:
                    raise RuntimeError(
                        "CAMS profile filename and GRIB valid time do not match"
                    )
                mapped = _map_cams_optics_to_model_grid(
                    optics, target_lats, target_lons
                )
                mapped_cache[path_key] = mapped
                source_paths.append(path_key)
            bracket_values.append(mapped)

        left_aod, left_profile, left_covered = bracket_values[0]
        right_aod, right_profile, right_covered = bracket_values[1]
        covered = left_covered & right_covered
        if not np.all(covered):
            raise RuntimeError(
                f"CAMS full profile does not cover every model cell at {valid_time:%Y-%m-%d %H:%MZ}"
            )
        right_weight = np.float32(selection.right_weight)
        mapped_aod = left_aod + (right_aod - left_aod) * right_weight
        mapped_profile = left_profile + (right_profile - left_profile) * right_weight
        prepared.grid[time_index, covered, NATIONAL_GRID_AEROSOL_AOD550] = mapped_aod[covered]
        aerosol_profiles[time_index, covered] = mapped_profile[covered]
        covered_cells += int(np.count_nonzero(covered))

    coverage = covered_cells / max(requested_cells, 1)
    point_stencil = _horizontal_stencil_for_coordinates(
        observers[:, 0],
        observers[:, 1],
        lat0=prepared.lat0,
        lon0=prepared.lon0,
        spacing=prepared.spacing,
        rows=prepared.rows,
        cols=prepared.cols,
    )
    local_aod = _horizontal_sample_fields(
        prepared,
        samples,
        point_stencil,
        (NATIONAL_GRID_AEROSOL_AOD550,),
    )[:, :, 0]
    samples[:, :, NATIONAL_SAMPLE_LOCAL_AOD] = np.where(
        valid, local_aod, samples[:, :, NATIONAL_SAMPLE_LOCAL_AOD]
    )

    anchor_brackets = {}
    for point_index, epoch in enumerate(np.asarray(anchor_epoch, dtype=np.float64)):
        target_time = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        bracket_times = cams_profile_bracket_times(target_time)
        selection = anchor_brackets.get(bracket_times)
        if selection is None:
            selection = find_cached_cams_bracket(
                cams_cache_dir,
                cams_area,
                target_time,
            )
            if selection is None:
                raise RuntimeError("No CAMS full-profile bracket covers the event anchor")
            anchor_brackets[bracket_times] = selection
        left_valid, right_valid = bracket_times
        right_weight = (
            0.0
            if left_valid == right_valid
            else (target_time - left_valid).total_seconds()
            / (right_valid - left_valid).total_seconds()
        )
        point_sources[point_index] = (
            f"cams-ads {selection.left.valid_time:%Y-%m-%d %H:%MZ}.."
            f"{selection.right.valid_time:%Y-%m-%d %H:%MZ} "
            f"w={right_weight:.6f}"
        )
        point_qualities[point_index] = "cams"
    return (
        np.ascontiguousarray(aerosol_profiles),
        sorted(set(source_paths)),
        point_sources,
        point_qualities,
        float(np.clip(coverage, 0.0, 1.0)),
    )


def validate_observer_profile_domain(
    prepared: NationalBatchPrepared,
    points: Sequence[dict[str, Any]],
) -> None:
    """Reject observer points outside the prepared model profile footprint.

    Ray-traced forward-field cells may still use their existing edge clamp, but
    an observer must have a real local profile.  Otherwise a point outside the
    profile is silently sampled from the nearest edge and can be published as
    a misleading zero score.
    """
    latitudes = np.asarray(prepared.latitudes, dtype=np.float64).reshape(-1)
    longitudes = np.asarray(prepared.longitudes, dtype=np.float64).reshape(-1)
    if latitudes.size == 0 or latitudes.shape != longitudes.shape:
        raise ValueError("prepared model profile coordinates are invalid")
    lat_min = float(np.min(latitudes))
    lat_max = float(np.max(latitudes))
    lon_min = float(np.min(longitudes))
    lon_max = float(np.max(longitudes))
    tolerance = 1e-6
    for index, point in enumerate(points):
        latitude = float(point["lat"])
        longitude = float(point["lon"])
        if not (
            lat_min - tolerance <= latitude <= lat_max + tolerance
            and lon_min - tolerance <= longitude <= lon_max + tolerance
        ):
            name = str(point.get("name") or f"point[{index}]")
            raise ValueError(
                f"observer {name!r} ({latitude:.6f}, {longitude:.6f}) is outside "
                f"the prepared model profile domain "
                f"[{lon_min:.6f}, {lon_max:.6f}, {lat_max:.6f}, {lat_min:.6f}]"
            )


def run_native_national_batch(
    prepared: NationalBatchPrepared,
    points: Sequence[dict[str, Any]],
    target_date: date,
    phenomenon: str,
    cycle_utc: datetime,
    radius_km: float,
    cams_cache_dir: Path,
    cams_area: list[float],
    compute_backend: str,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    list[str],
    list[str],
    list[str],
]:
    validate_observer_profile_domain(prepared, points)
    observers, samples, evaluation_epoch, anchor_epoch = prepare_solar_samples(
        points, target_date, phenomenon, cycle_utc, prepared.hours
    )
    # The public timeline stays on its canonical 20 five-minute samples so
    # frame assembly and duration scoring keep their existing contract.  The
    # native scorer only needs the smallest batch-wide span containing a valid
    # sample; universally invalid leading/trailing slices would otherwise
    # allocate and enter the native observer loop for every point.
    valid_steps = samples[:, :, NATIONAL_SAMPLE_VALID] >= 0.5
    active_step_columns = np.flatnonzero(np.any(valid_steps, axis=0))
    if active_step_columns.size == 0:
        raise RuntimeError("National scoring requires at least one valid solar sample")
    native_step_start = int(active_step_columns[0])
    native_step_stop = int(active_step_columns[-1]) + 1
    native_samples = np.ascontiguousarray(
        samples[:, native_step_start:native_step_stop, :], dtype=np.float32
    )
    (
        aerosol_profiles,
        optical_profile_sources,
        sources,
        qualities,
        _optical_profile_coverage,
    ) = _apply_cams_aerosol_profiles(
        prepared,
        native_samples,
        observers,
        anchor_epoch,
        cycle_utc,
        cams_cache_dir,
        cams_area,
    )
    # _apply_cams_aerosol_profiles fills NATIONAL_SAMPLE_LOCAL_AOD in the
    # tensor it receives. When the trimmed window is a non-trivial slice that
    # tensor is a contiguous copy, so write the filled column back into the
    # canonical samples tensor that frame assembly and the point core publish;
    # otherwise the published AOD stays on the prepare_solar_samples heuristic
    # default instead of the CAMS value.
    samples[
        :, native_step_start:native_step_stop, NATIONAL_SAMPLE_LOCAL_AOD
    ] = native_samples[:, :, NATIONAL_SAMPLE_LOCAL_AOD]

    native_output = national_assess_batch(
        prepared.grid,
        aerosol_profiles,
        observers,
        native_samples,
        rows=prepared.rows,
        cols=prepared.cols,
        lat0=prepared.lat0,
        lon0=prepared.lon0,
        spacing=prepared.spacing,
        radius_km=radius_km,
        compute_backend=compute_backend,
    )
    # Reinsert the trimmed columns as zero rows.  Invalid native rows already
    # have this representation, so the returned tensor remains bit-compatible
    # with the canonical fixed-width path.
    output = np.zeros(
        (native_output.shape[0], samples.shape[1], native_output.shape[2]),
        dtype=native_output.dtype,
    )
    output[:, native_step_start:native_step_stop, :] = native_output
    return (
        output,
        samples,
        evaluation_epoch,
        anchor_epoch,
        sources,
        qualities,
        sorted(set(optical_profile_sources)),
    )


REGIONAL_CLOUD_MATERIAL_CLASSES = [
    "none",
    "ice-dominant-thin-upper",
    "layered-middle",
    "layered-upper",
    "deep-convective-anvil",
    "middle-cloud",
    "shallow-low-layer",
    "low-cloud",
    "deep-cloud-shield",
]

PRESSURE_FAMILY_NAMES: tuple[str | None, ...] = (None, "high", "middle", "low")


def pressure_family_name(value: float) -> str | None:
    code = int(round(float(value)))
    return PRESSURE_FAMILY_NAMES[code] if 0 < code < len(PRESSURE_FAMILY_NAMES) else None


def cloud_material_class_name(value: float) -> str:
    index = int(round(value))
    return (
        REGIONAL_CLOUD_MATERIAL_CLASSES[index]
        if 0 <= index < len(REGIONAL_CLOUD_MATERIAL_CLASSES)
        else "none"
    )


def _regional_cloud_phase_diagnosis(
    cloud_ice_kg_kg: float | None,
    cloud_liquid_kg_kg: float | None,
) -> dict[str, Any]:
    """Diagnose phase only from published condensate for the same target."""
    diagnosis = cloud_phase_diagnosis(cloud_ice_kg_kg, cloud_liquid_kg_kg)
    diagnosis["source"] = (
        "same-target-condensate-unavailable"
        if diagnosis["label"] == "unknown"
        else "gfs-pressure-level-condensate-same-target"
    )
    return diagnosis


class _ProfileDiagnosticBundle(NamedTuple):
    """All profile diagnostics derived from one sampled profile per step."""

    cloud_material_class_code: np.ndarray
    liquid: np.ndarray
    ice: np.ndarray
    deep: np.ndarray
    support_count: np.ndarray
    uncertainty_m: np.ndarray
    confidence: np.ndarray


def _profile_diagnostic_bundle(
    prepared: NationalBatchPrepared,
    samples: np.ndarray,
    stencil: _HorizontalStencil,
) -> _ProfileDiagnosticBundle | None:
    """Derive vertical material class, deep risk, and cloud geometry diagnostics.

    A single pressure-level member still carries a broad uncertainty interval,
    but the GFS aggregate cloud-boundary pressure fields provide an independent
    envelope and halve that unresolved interval when present. This confidence
    output is metadata only; it is never applied as a second score penalty.

    The profile and aggregate fields are sampled once per step. The large
    sampled profile is consumed immediately and is not retained across steps.
    """
    if prepared.layer_profiles is None:
        return None
    profiles = prepared.layer_profiles
    point_count, step_count = samples.shape[:2]
    pressure_levels = np.asarray(prepared.pressure_levels, dtype=np.float32)
    if pressure_levels.size != profiles.shape[2]:
        pressure_levels = np.asarray(
            profiles[0, 0, :, _PROFILE_PRESSURE], dtype=np.float32
        )
    level_depths = np.asarray(
        [
            _LEVEL_DEPTH_M.get(int(round(float(level))), 700.0)
            for level in pressure_levels
        ],
        dtype=np.float32,
    )
    aggregate_fields = prepared.grid[:, :, (14, 17, 18, 19, 20, 21, 22)]
    codes = np.zeros((point_count, step_count), dtype=np.uint8)
    liquid = np.zeros((point_count, step_count), dtype=np.float32)
    ice = np.zeros((point_count, step_count), dtype=np.float32)
    deep = np.zeros((point_count, step_count), dtype=np.float32)
    support_count = np.zeros((point_count, step_count), dtype=np.int16)
    uncertainty_m = np.zeros((point_count, step_count), dtype=np.float32)
    confidence = np.ones((point_count, step_count), dtype=np.float32)

    for step_index in range(step_count):
        sample = samples[:, step_index]
        profile = _horizontal_sample_tensor_step(
            profiles,
            sample,
            stencil,
        )
        pressure_for_structure = pressure_levels
        target_amount, _, _, _, explicit_fields = _effective_profile_cloud_fields(profile)
        aggregate_frame = _horizontal_sample_tensor_step(
            aggregate_fields,
            sample,
            stencil,
        )
        boundary_values = aggregate_frame[:, 1:]
        aggregate_deep_risk = _aggregate_deep_cloud_risk(
            target_amount,
            pressure_for_structure,
            np.nanmax(profile[..., _PROFILE_MIDDLE_FRACTION], axis=-1),
            np.nanmax(profile[..., _PROFILE_HIGH_FRACTION], axis=-1),
            (
                (boundary_values[:, 0], boundary_values[:, 1]),
                (boundary_values[:, 2], boundary_values[:, 3]),
                (boundary_values[:, 4], boundary_values[:, 5]),
            ),
            aggregate_frame[:, 0],
        )
        structure = _vertical_cloud_structure(
            target_amount,
            pressure_for_structure,
            profile[:, :, _PROFILE_HEIGHT],
            profile[:, :, _PROFILE_LIQUID],
            profile[:, :, _PROFILE_ICE],
            profile[:, :, _PROFILE_OMEGA],
            explicit_fields,
            aggregate_deep_risk,
        )
        codes[:, step_index] = structure[5]

        cloudy = target_amount >= _PROFILE_DECK_MEMBER_AMOUNT
        liquid_values = np.maximum(
            np.nan_to_num(profile[..., _PROFILE_LIQUID], nan=0.0), 0.0
        )
        ice_values = np.maximum(
            np.nan_to_num(profile[..., _PROFILE_ICE], nan=0.0), 0.0
        )
        liquid[:, step_index] = np.max(
            np.where(cloudy, liquid_values, 0.0), axis=-1
        )
        ice[:, step_index] = np.max(
            np.where(cloudy, ice_values, 0.0), axis=-1
        )
        # ``explicit_fields`` only affects the cloud-type selectors inside the
        # structure helper; deep risk is resolved before that mask is read.
        # Reuse the same result that produced ``cloud_material_class_code``.
        deep[:, step_index] = np.clip(structure[3], 0.0, 1.0)

        # Geometry support is broader than a coherent/deep deck. A sparse
        # pressure-level cloud still constrains the proxy base/top, but its
        # confidence remains low through the one-level uncertainty branch.
        cloudy = (
            target_amount >= ACTIVE_CLOUD_THRESHOLD
        ) & np.isfinite(profile[..., _PROFILE_HEIGHT])
        count = np.count_nonzero(cloudy, axis=-1).astype(np.int16)
        support_count[:, step_index] = count
        aggregate_boundary_support = np.any(
            np.isfinite(boundary_values) & (boundary_values > 0.0), axis=-1
        )
        active_depth = np.max(
            np.where(cloudy, level_depths[None, :], 0.0), axis=-1
        )
        # One pressure sample is a full layer-depth proxy. With two or more
        # samples, the profile constrains the deck but still leaves a boundary
        # uncertainty of roughly half the coarsest represented layer.
        uncertainty_m[:, step_index] = np.where(
            count <= 0,
            0.0,
            np.where(count == 1, active_depth, 0.5 * active_depth),
        )
        # A GFS aggregate boundary is an additional geometric constraint, not
        # a second cloud amount. It halves the unresolved vertical interval
        # while leaving the quality score untouched.
        boundary_factor = (
            0.75
            if prepared.boundary_temporal_semantics == "average-envelope-not-instantaneous"
            else 0.5
        )
        uncertainty_m[:, step_index] = np.where(
            aggregate_boundary_support,
            boundary_factor * uncertainty_m[:, step_index],
            uncertainty_m[:, step_index],
        )
        peak_amount = np.max(
            np.where(cloudy, np.clip(target_amount, 0.0, 1.0), 0.0), axis=-1
        )
        support_confidence = np.select(
            [count <= 0, count == 1, count == 2],
            [1.0, 0.55, 0.75],
            default=0.90,
        )
        boundary_confidence = (
            np.full_like(support_confidence, 0.75)
            if prepared.boundary_temporal_semantics == "average-envelope-not-instantaneous"
            else np.maximum(support_confidence, 0.85)
        )
        support_confidence = np.where(
            aggregate_boundary_support,
            boundary_confidence,
            support_confidence,
        )
        amount_confidence = 0.85 + 0.15 * peak_amount
        confidence[:, step_index] = np.clip(
            support_confidence * amount_confidence,
            0.0,
            1.0,
        )
        del (
            aggregate_frame,
            boundary_values,
            profile,
            structure,
            target_amount,
            explicit_fields,
            aggregate_deep_risk,
            cloudy,
            liquid_values,
            ice_values,
            count,
            aggregate_boundary_support,
            active_depth,
            peak_amount,
            support_confidence,
            amount_confidence,
        )
    return _ProfileDiagnosticBundle(
        codes,
        liquid,
        ice,
        deep,
        support_count,
        uncertainty_m,
        confidence,
    )


def _national_confidence(
    aerosol_quality: str,
    vertical_proxy_confidence: float,
) -> float:
    """Combine data-source confidence with pressure-level geometry support."""
    aerosol_base = 0.82 if str(aerosol_quality).lower() == "cams" else 0.60
    geometry = float(np.clip(vertical_proxy_confidence, 0.0, 1.0))
    # Keep the source baseline meaningful while exposing unresolved vertical
    # structure; this is metadata confidence, not a quality-index multiplier.
    return float(np.clip(aerosol_base * (0.70 + 0.30 * geometry), 0.25, 0.96))


def _representative_quality_aod(quality: dict[str, Any]) -> float:
    """Expose the exact event-window AOD emitted by the current scorer."""
    components = quality["components"]
    if not isinstance(components, dict):
        raise ValueError("current national quality components must be an object")
    raw_value = components["quality_aod550"]
    if isinstance(raw_value, bool) or not isinstance(raw_value, Real):
        raise ValueError("current national quality_aod550 must be a finite number")
    value = float(raw_value)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("current national quality_aod550 must be finite and non-negative")
    return value


def _iso_timestamp(epoch: float, timezone_name: str) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(ZoneInfo(timezone_name)).isoformat()


def _encoded_u8(values: np.ndarray) -> str:
    quantized = np.rint(np.clip(np.nan_to_num(values, nan=0.0), 0.0, 1.0) * 255.0).astype(np.uint8)
    return base64.b64encode(np.ascontiguousarray(quantized).tobytes()).decode("ascii")


def _encoded_u16(values: np.ndarray, scale: float = 1.0) -> str:
    quantized = np.rint(np.maximum(np.nan_to_num(values, nan=0.0), 0.0) / scale)
    quantized = np.clip(quantized, 0.0, np.iinfo(np.uint16).max).astype("<u2")
    return base64.b64encode(np.ascontiguousarray(quantized).tobytes()).decode("ascii")


def _encoded_i16(values: np.ndarray, scale: float = 1.0) -> str:
    quantized = np.rint(np.nan_to_num(values, nan=0.0) / scale)
    quantized = np.clip(
        quantized, np.iinfo(np.int16).min, np.iinfo(np.int16).max
    ).astype("<i2")
    return base64.b64encode(np.ascontiguousarray(quantized).tobytes()).decode("ascii")


def _encoded_raw_u8(values: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(values, dtype=np.uint8).tobytes()).decode("ascii")


def _fire_cloud_quality(
    values: np.ndarray,
    samples: np.ndarray,
    offsets_minutes: np.ndarray,
    valid_indices: np.ndarray,
    burnable_gate: np.ndarray | None = None,
    score_ceiling: np.ndarray | None = None,
    *,
    apply_duration: bool = True,
    analysis_radius_km: float = NATIONAL_ANALYSIS_RADIUS_KM,
    instant_components: dict[str, np.ndarray] | None = None,
    local_instant_components: dict[str, np.ndarray] | None = None,
    precomputed_score: dict[str, object] | None = None,
) -> dict[str, Any]:
    values = np.asarray(values)
    samples = np.asarray(samples)
    if values.ndim != 2 or values.shape[1] != OUTPUT_FIELD_COUNT:
        raise ValueError(
            "_fire_cloud_quality requires the current observer-field output "
            f"layout with {OUTPUT_FIELD_COUNT} fields; got {values.shape}"
        )
    if samples.ndim != 2 or samples.shape[1] != NATIONAL_SAMPLE_FIELDS:
        raise ValueError(
            f"_fire_cloud_quality requires {NATIONAL_SAMPLE_FIELDS} sample fields; "
            f"got {samples.shape}"
        )
    if valid_indices.size == 0:
        return {"index": 0.0, "level": fire_cloud_quality_level(0.0), "components": {}}

    selected = np.asarray(values[valid_indices], dtype=np.float64)
    view = selected[:, NATIONAL_VIEW_AMOUNT_OFFSET]
    burn = selected[:, NATIONAL_BURN_AMOUNT_OFFSET]
    load = selected[:, NATIONAL_BURN_LOAD_OFFSET]
    material_load = selected[:, NATIONAL_MATERIAL_LOAD_OFFSET]
    incoming_aerosol_load = selected[:, NATIONAL_INCOMING_AEROSOL_LOAD_OFFSET]
    observer_return_optical_depth = np.maximum(
        selected[:, NATIONAL_OBSERVER_RETURN_OPTICAL_DEPTH_OFFSET], 0.0
    )
    observer_return_transmission = np.clip(
        selected[:, NATIONAL_OBSERVER_RETURN_TRANSMISSION_OFFSET], 0.0, 1.0
    )
    local_return_optical_depth = np.maximum(
        selected[:, NATIONAL_LOCAL_RETURN_OPTICAL_DEPTH_OFFSET], 0.0
    )
    local_return_transmission = np.clip(
        selected[:, NATIONAL_LOCAL_RETURN_TRANSMISSION_OFFSET], 0.0, 1.0
    )
    incoming_block = np.clip(selected[:, NATIONAL_INCOMING_BLOCK_OFFSET], 0.0, 1.0)
    visibility = np.clip(selected[:, 5], 0.0, 1.0)
    vividness = np.clip(selected[:, 4], 0.0, 1.0)
    local_aod = max(0.0, float(np.mean(samples[valid_indices, 5])))
    score = (
        score_observer_cloud_field(
            view,
            burn,
            load,
            material_load,
            incoming_aerosol_load,
            incoming_block,
            visibility,
            vividness,
            np.asarray(offsets_minutes[valid_indices], dtype=np.float64),
            local_aod,
            burnable_gate=(
                burnable_gate[valid_indices] if burnable_gate is not None else None
            ),
            score_ceiling=(
                score_ceiling[valid_indices] if score_ceiling is not None else None
            ),
            analysis_radius_km=analysis_radius_km,
            apply_duration=apply_duration,
            local_view_amount=selected[:, NATIONAL_LOCAL_VIEW_OFFSET],
            local_burnable_amount=selected[:, NATIONAL_LOCAL_BURN_OFFSET],
            local_burnable_load=selected[:, NATIONAL_LOCAL_LOAD_OFFSET],
            local_burnable_material_load=selected[:, NATIONAL_LOCAL_MATERIAL_LOAD_OFFSET],
            local_burnable_incoming_aerosol_load=selected[
                :, NATIONAL_LOCAL_INCOMING_AEROSOL_LOAD_OFFSET
            ],
            _instant_components=(
                {
                    name: np.asarray(component)[valid_indices]
                    for name, component in instant_components.items()
                }
                if instant_components is not None
                else None
            ),
            _local_instant_components=(
                {
                    name: np.asarray(component)[valid_indices]
                    for name, component in local_instant_components.items()
                }
                if local_instant_components is not None
                else None
            ),
        )
        if precomputed_score is None
        else precomputed_score
    )
    if not isinstance(score, dict) or set(score) != {"index", "components"}:
        raise ValueError("current observer-field score must contain exactly index and components")
    score_index = score["index"]
    if (
        isinstance(score_index, bool)
        or not isinstance(score_index, Real)
        or not math.isfinite(float(score_index))
        or not 0.0 <= float(score_index) <= 2.5
    ):
        raise ValueError("current observer-field score index must be finite within 0..2.5")
    score_components = score["components"]
    expected_score_component_fields = OBSERVER_CLOUD_FIELD_COMPONENT_FIELDS | (
        OBSERVER_CLOUD_FIELD_SCORE_CEILING_COMPONENT_FIELDS
        if score_ceiling is not None
        else frozenset()
    )
    if not isinstance(score_components, dict):
        raise ValueError("current observer-field score components must be an object")
    actual_score_component_fields = set(score_components)
    if actual_score_component_fields != expected_score_component_fields:
        missing_fields = sorted(
            expected_score_component_fields - actual_score_component_fields
        )
        extra_fields = sorted(
            actual_score_component_fields - expected_score_component_fields
        )
        raise ValueError(
            "current observer-field score components do not match the exact contract: "
            f"missing={missing_fields}, extra={extra_fields}"
        )
    effective_coverage = score_components["burnable_cloud_coverage_effective"]
    if (
        isinstance(effective_coverage, bool)
        or not isinstance(effective_coverage, Real)
        or not math.isfinite(float(effective_coverage))
        or not 0.0 <= float(effective_coverage) <= 1.0
    ):
        raise ValueError(
            "current observer-field score components require finite "
            "burnable_cloud_coverage_effective within 0..1"
        )
    components = dict(score_components)

    # The native ray-traced burn field is authoritative. Rebuild the same
    # gates the authoritative scorers apply before argmax/active — the
    # observer-visibility floor (MIN_OBSERVER_VISIBILITY_FOR_SCORE, applied
    # inside the batch scorer via arrays[6]), the external burnability veto,
    # and the non-burnable blocker consistency cap — so precomputed batch
    # scores and scalar diagnostics select the same event-active instant.
    scorer_burn = np.clip(np.asarray(burn, dtype=np.float64), 0.0, 1.0)
    incoming_block_rebuild = np.clip(
        selected[:, NATIONAL_INCOMING_BLOCK_OFFSET], 0.0, 1.0
    )
    visibility_gate = visibility >= MIN_OBSERVER_VISIBILITY_FOR_SCORE
    effective_burn = np.where(visibility_gate, scorer_burn, 0.0)
    if burnable_gate is not None:
        effective_gate = np.asarray(burnable_gate, dtype=bool)[valid_indices]
        effective_burn = np.where(effective_gate, effective_burn, 0.0)
    blocker_ceiling = np.where(
        incoming_block_rebuild > 1e-9,
        np.clip(
            view * (1.0 - incoming_block_rebuild + NONBURNABLE_BLOCKER_TOLERANCE),
            0.0,
            1.0,
        ),
        1.0,
    )
    effective_burn = np.minimum(effective_burn, blocker_ceiling)

    peak = int(np.argmax(effective_burn))
    active = _event_active_cloud(effective_burn)
    ground_view = selected[:, NATIONAL_GROUND_VIEW_OFFSET]
    ground_burn = selected[:, NATIONAL_GROUND_BURN_OFFSET]
    ground_load = selected[:, NATIONAL_GROUND_LOAD_OFFSET]
    incoming_block = np.clip(
        selected[:, NATIONAL_INCOMING_BLOCK_OFFSET], 0.0, 1.0
    )
    layer_diagnostics: dict[str, float] = {}
    for label, offset in (
        ("high_view", NATIONAL_HIGH_VIEW_OFFSET),
        ("mid_view", NATIONAL_MID_VIEW_OFFSET),
        ("low_view", NATIONAL_LOW_VIEW_OFFSET),
        ("high_burn", NATIONAL_HIGH_BURN_OFFSET),
        ("mid_burn", NATIONAL_MID_BURN_OFFSET),
        ("low_burn", NATIONAL_LOW_BURN_OFFSET),
    ):
        values_for_layer = np.clip(selected[:, offset], 0.0, 1.0)
        layer_diagnostics[f"forward_{label}_peak"] = float(np.max(values_for_layer))
        layer_diagnostics[f"forward_{label}_mean"] = float(np.mean(values_for_layer))
    components.update({
        **NATIONAL_CLOUD_OPTICS_METADATA,
        "solar_incoming_block_risk_at_best_cloud": float(incoming_block[peak]),
        "observer_return_optical_depth_mean": (
            _active_time_mean(
                observer_return_optical_depth,
                offsets_minutes[valid_indices],
                active,
            )
            if np.any(active)
            else 0.0
        ),
        "observer_return_transmission_mean": (
            _active_time_mean(
                observer_return_transmission,
                offsets_minutes[valid_indices],
                active,
            )
            if np.any(active)
            else 0.0
        ),
        "observer_local_return_optical_depth_mean": (
            _active_time_mean(
                local_return_optical_depth,
                offsets_minutes[valid_indices],
                active,
            )
            if np.any(active)
            else 0.0
        ),
        "observer_local_return_transmission_mean": (
            _active_time_mean(
                local_return_transmission,
                offsets_minutes[valid_indices],
                active,
            )
            if np.any(active)
            else 0.0
        ),
        "observer_return_model": "raw_beer_lambert_diagnostic; no cloud/sky radiance or perception mapping",
        "ground_area_view_amount_mean": float(np.mean(ground_view)),
        "ground_area_burnable_amount_mean": float(np.mean(ground_burn)),
        "ground_area_burnable_load_mean": float(np.mean(ground_load)),
        **layer_diagnostics,
    })
    return {
        "index": float(score_index),
        "level": fire_cloud_quality_level(float(score_index)),
        "components": components,
    }


def _instant_fire_cloud_quality(
    values: np.ndarray,
    samples: np.ndarray,
    offsets_minutes: np.ndarray,
    step_index: int,
    burnable_gate: np.ndarray | None = None,
    score_ceiling: np.ndarray | None = None,
    *,
    analysis_radius_km: float = NATIONAL_ANALYSIS_RADIUS_KM,
) -> dict[str, Any]:
    return _fire_cloud_quality(
        values,
        samples,
        offsets_minutes,
        np.asarray([step_index], dtype=np.int64),
        burnable_gate,
        score_ceiling,
        apply_duration=False,
        analysis_radius_km=analysis_radius_km,
    )


def fire_cloud_quality_from_assessments(
    steps: Sequence[Any],
    aod550: float,
    *,
    event_time_local: datetime | None = None,
    core_window_minutes: float | None = None,
    apply_duration: bool = True,
) -> dict[str, Any]:
    count = len(steps)
    if count == 0:
        return {
            "quality_index": 0.0,
            "quality_level": fire_cloud_quality_level(0.0),
            "quality_components": {},
            "duration_status": "estimated" if apply_duration else "unknown",
            "steps": [],
        }
    values = np.zeros((count, OUTPUT_FIELD_COUNT), dtype=np.float32)
    samples = np.zeros((count, NATIONAL_SAMPLE_FIELDS), dtype=np.float32)
    offsets = np.asarray(
        [
            (step.valid_time_local - steps[0].valid_time_local).total_seconds() / 60.0
            for step in steps
        ],
        dtype=np.float32,
    )
    burnable_gate = np.asarray(
        [bool(getattr(step, "observation_burnable", True)) for step in steps],
        dtype=bool,
    )
    assessment_ceilings = [
        getattr(step, "observation_score_ceiling", None) for step in steps
    ]
    score_ceiling = (
        np.asarray(
            [2.5 if ceiling is None else float(ceiling) for ceiling in assessment_ceilings],
            dtype=np.float32,
        )
        if any(ceiling is not None for ceiling in assessment_ceilings)
        else None
    )
    point_path_diagnostics: list[dict[str, float | None]] = []
    for index, step in enumerate(steps):
        values[index, 2] = float(getattr(step, "intensity_score", 0.0))
        view_amount = float(np.clip(step.forward_view_cloud_field_amount, 0.0, 1.0))
        burnable_amount = float(
            np.clip(step.forward_burnable_cloud_field_amount, 0.0, 1.0)
        )
        local_view_amount = float(
            np.clip(step.observer_local_cloud_field_amount, 0.0, 1.0)
        )
        incoming_block = float(np.clip(
            getattr(step, "solar_incoming_block_risk", 0.0), 0.0, 1.0
        ))
        values[index, 3] = burnable_amount
        values[index, 4] = float(getattr(step, "vividness_score", 0.0))
        values[index, 5] = float(getattr(step, "visibility_score", 0.0))
        values[index, 37] = 1.0 - incoming_block
        values[index, 38] = 1.0
        values[index, 39] = incoming_block
        # The ratio is derived from the same two authoritative field amounts
        # that feed the scorer.  Reusing the assessment's legacy ratio lets a
        # stale zero survive beside a non-zero burnable field.
        values[index, 40] = float(np.clip(
            burnable_amount / max(view_amount, 1e-6) if view_amount > 0.0 else 0.0,
            0.0,
            1.0,
        ))
        values[index, 42] = values[index, 5]
        values[index, NATIONAL_VIEW_AMOUNT_OFFSET] = view_amount
        values[index, NATIONAL_BURN_AMOUNT_OFFSET] = burnable_amount
        values[index, NATIONAL_LOCAL_VIEW_OFFSET] = local_view_amount
        load_value = getattr(step, "forward_burnable_cloud_load", None)
        material_load_value = getattr(
            step, "forward_burnable_cloud_material_load", None
        )
        top_candidate = getattr(step, "top_candidate", None)
        top_boundary = getattr(step, "top_boundary", None)
        top_cloud_hole = getattr(step, "top_cloud_hole", None)
        # GFS point assessments retain their existing selected-structure
        # material path.  Cloud-type burnability is only that path's fallback;
        # it is not a satellite optical-depth measurement.
        material_quality = float(np.clip(
            (
                getattr(top_candidate, "cloud_type_burnability_score", None)
                if top_candidate is not None
                else None
            )
            or (
                getattr(top_boundary, "cloud_type_burnability_score", None)
                if top_boundary is not None
                else None
            )
            or (
                getattr(top_cloud_hole, "rear_target_burnability_score", None)
                if top_cloud_hole is not None
                else None
            )
            or 0.0,
            0.0,
            1.0,
        ))
        satellite_material = getattr(
            step, "satellite_retrieval_material_evidence", None
        )
        if satellite_material is not None:
            # Calibrated satellite cloud columns establish cloud presence but
            # HSD does not publish CLWMR/ICMR or COT.  The explicit evidence is
            # the one neutral material reference; candidate cloud type has
            # already been consumed by burnability/selection and cannot be
            # reused as optical thickness.
            material_quality = float(np.clip(satellite_material, 0.0, 1.0))
        derived_material_load = burnable_amount * 2.5 * material_quality
        values[index, NATIONAL_MATERIAL_LOAD_OFFSET] = float(np.clip(
            derived_material_load
            if material_load_value is None
            else material_load_value,
            0.0,
            2.5,
        ))
        values[index, NATIONAL_BURN_LOAD_OFFSET] = float(np.clip(
            values[index, NATIONAL_MATERIAL_LOAD_OFFSET]
            if load_value is None
            else load_value,
            0.0,
            2.5,
        ))
        incoming_aerosol_load_value = getattr(
            step, "forward_burnable_cloud_incoming_aerosol_load", None
        )
        values[index, NATIONAL_INCOMING_AEROSOL_LOAD_OFFSET] = float(np.clip(
            values[index, NATIONAL_MATERIAL_LOAD_OFFSET]
            if incoming_aerosol_load_value is None
            else incoming_aerosol_load_value,
            0.0,
            2.5,
        ))
        values[index, NATIONAL_INCOMING_BLOCK_OFFSET] = incoming_block
        samples[index, 5] = float(aod550)
        # The point scorer already selected the physical regime and integrated
        # the real two-leg path for that regime. Keep its selected values as
        # diagnostics, but never feed them back into the generic forward-field
        # formula: national scoring uses native load/material ratios instead of
        # this selected-target proxy.
        point_path_diagnostics.append({
            "observer_path_aod550": (
                float(getattr(step, "selected_observer_path_aod550"))
                if getattr(step, "selected_observer_path_aod550", None) is not None
                else None
            ),
            "inflow_path_aod550": (
                float(getattr(step, "selected_inflow_path_aod550"))
                if getattr(step, "selected_inflow_path_aod550", None) is not None
                else None
            ),
            "aerosol_path_variability": (
                float(getattr(step, "selected_aerosol_path_variability_score", 0.0))
            ),
        })
    valid_indices = np.arange(count, dtype=np.int64)
    if event_time_local is not None and core_window_minutes is not None:
        core_indices = np.asarray(
            [
                index
                for index, step in enumerate(steps)
                if abs((step.valid_time_local - event_time_local).total_seconds())
                <= float(core_window_minutes) * 60.0
            ],
            dtype=np.int64,
        )
        if core_indices.size:
            valid_indices = core_indices
    event = _fire_cloud_quality(
        values,
        samples,
        offsets,
        valid_indices,
        burnable_gate,
        score_ceiling,
        apply_duration=apply_duration,
    )
    selected_point_path_diagnostics = [
        point_path_diagnostics[int(index)] for index in valid_indices
    ]
    point_observer_aod = [
        value["observer_path_aod550"]
        for value in selected_point_path_diagnostics
        if value["observer_path_aod550"] is not None
    ]
    point_inflow_aod = [
        value["inflow_path_aod550"]
        for value in selected_point_path_diagnostics
        if value["inflow_path_aod550"] is not None
    ]
    point_path_variability = [
        value["aerosol_path_variability"]
        for value in selected_point_path_diagnostics
        if value["aerosol_path_variability"] is not None
    ]
    event["components"].update({
        "observer_path_aod550": float(np.mean(point_observer_aod)) if point_observer_aod else None,
        "inflow_path_aod550": float(np.mean(point_inflow_aod)) if point_inflow_aod else None,
        "aerosol_path_variability": float(np.mean(point_path_variability)) if point_path_variability else None,
        "point_path_diagnostics_source": "scoring-selected-regime; event-window-valid-steps; diagnostic-only",
    })
    quality_steps = []
    for index in range(count):
        instant = _instant_fire_cloud_quality(
            values,
            samples,
            offsets,
            index,
            burnable_gate,
            score_ceiling,
        )
        components = instant["components"]
        burnable = components.get("physically_burnable_steps", 0.0) > 0.0
        quality_steps.append({
            "instant_quality_index": float(instant["index"]),
            "instant_quality_level": str(instant["level"]),
            "physically_burnable": bool(burnable),
            "observation_burnable": bool(burnable_gate[index]),
            "burnable_cloud_coverage": float(components.get("burnable_cloud_coverage_peak", 0.0)),
            "geometric_cloud_coverage": float(values[index, 3]),
            "forward_view_cloud_field_amount": float(values[index, NATIONAL_VIEW_AMOUNT_OFFSET]),
            "forward_burnable_cloud_field_amount": float(
                components.get("forward_burnable_cloud_field_amount_peak", 0.0)
            ),
            "observer_local_cloud_field_amount": float(
                values[index, NATIONAL_LOCAL_VIEW_OFFSET]
            ),
            "forward_burnable_cloud_load": float(
                components.get("forward_burnable_cloud_load_peak", 0.0)
            ),
            "forward_burnable_cloud_material_load": float(
                components.get("forward_burnable_cloud_material_load_peak", 0.0)
            ),
            "forward_burnable_cloud_incoming_aerosol_load": float(
                components.get("forward_burnable_cloud_incoming_aerosol_load_mean", 0.0)
            ),
            "solar_incoming_block_risk": float(values[index, NATIONAL_INCOMING_BLOCK_OFFSET]),
            "forward_burnable_cloud_ratio": float(
                components.get("forward_burnable_cloud_ratio_peak", 0.0)
            ),
            "brightness_color": float(components.get("brightness_color", 0.0)),
            "cloud_material_score": float(components.get("cloud_material", 0.0)),
            "quality_aod550": float(components.get("quality_aod550", aod550)),
            "observer_return_transmission": (
                float(getattr(steps[index], "observer_return_transmission"))
                if getattr(steps[index], "observer_return_transmission", None) is not None
                else None
            ),
            **point_path_diagnostics[index],
        })
    scored_indices = [int(index) for index in valid_indices]
    best_index = max(
        (
            index
            for index in scored_indices
            if index < len(quality_steps)
            and quality_steps[index]["physically_burnable"]
        ),
        key=lambda index: quality_steps[index]["instant_quality_index"],
        default=None,
    )
    best = quality_steps[best_index] if best_index is not None else {}
    return {
        "quality_index": float(event["index"]),
        "quality_level": str(event["level"]),
        "quality_components": event["components"],
        "duration_status": "estimated" if apply_duration else "unknown",
        "best_index": best_index,
        "steps": quality_steps,
    }


def build_national_frame_payloads(
    prepared: NationalBatchPrepared,
    points: Sequence[dict[str, Any]],
    target_date: date,
    phenomenon: str,
    cycle_utc: datetime,
    output: np.ndarray,
    samples: np.ndarray,
    evaluation_epoch: np.ndarray,
    anchor_epoch: np.ndarray,
    aerosol_sources: Sequence[str],
    aerosol_qualities: Sequence[str],
    compute_backend: str,
    *,
    include_point_core: bool,
    point_spacing_deg: float | None = None,
    analysis_radius_km: float = NATIONAL_ANALYSIS_RADIUS_KM,
    progress_callback=None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any]]:
    """Build compact frames from the same complete forward-field result as points."""
    if prepared.model_source not in {"gfs", "ec"}:
        raise ValueError("national prediction frames require a supported numeric model")
    samples = np.asarray(samples)
    if samples.ndim != 3 or samples.shape[2] != NATIONAL_SAMPLE_FIELDS:
        raise ValueError(
            f"national samples must have {NATIONAL_SAMPLE_FIELDS} fields"
        )
    point_count, step_count = output.shape[:2]
    if not isinstance(include_point_core, bool):
        raise ValueError("include_point_core must be boolean")
    if include_point_core and point_count != 1:
        raise ValueError("point core output requires exactly one observer")
    expected_offsets = np.asarray(
        national_event_offsets_minutes(phenomenon), dtype=np.float64
    )
    evaluation_epoch = np.asarray(evaluation_epoch, dtype=np.float64)
    anchor_epoch = np.asarray(anchor_epoch, dtype=np.float64)
    if evaluation_epoch.shape != (point_count, step_count):
        raise ValueError("evaluation_epoch must match the complete point/time tensor")
    if anchor_epoch.shape != (point_count,):
        raise ValueError("anchor_epoch must contain exactly one event anchor per point")
    if step_count != expected_offsets.size:
        raise ValueError(
            "national frames must use the complete canonical event timeline: "
            f"expected {expected_offsets.size} steps, got {step_count}"
        )
    point_offsets = (evaluation_epoch - anchor_epoch[:, None]) / 60.0
    if not np.allclose(
        point_offsets,
        expected_offsets[None, :],
        rtol=0.0,
        atol=1e-6,
    ):
        raise ValueError(
            "evaluation_epoch offsets do not match the canonical event timeline"
        )
    frame_started = time.monotonic()
    # national_assess_batch already executed the authoritative observer-field Rust
    # all-cell field pass. A second point refinement would make regional and
    # point responses follow different rules.
    refinement_mode = "raytraced-sky-v4-cpu"
    if progress_callback is not None:
        progress_callback({
            "stage": "cloud_field",
            "percent": 42,
            "message": f"Rust 全量观测云场 {point_count} 个点",
            "completed": point_count,
            "total": point_count,
        })
    valid_mask = output[:, :, 0] >= 0.5
    processed_samples = int(np.count_nonzero(valid_mask))
    # Duration correction is computed from the complete burnable-field
    # curve in _fire_cloud_quality.  Do not mutate the native field here with
    # the former isolated-peak persistence filter; that made frame and point
    # requests disagree.
    model_source = prepared.model_source
    selected_column_diagnostic_source = (
        NATIONAL_SELECTED_COLUMN_DIAGNOSTIC_SOURCE
        if model_source == "gfs"
        else EC_SELECTED_COLUMN_DIAGNOSTIC_SOURCE
    )
    cloud_optics_metadata = cloud_optics_metadata_for_model(model_source)
    stencil = _horizontal_stencil(prepared, points)
    # These are the only selected-column diagnostics used by frame assembly.
    # Native CloudLayerSample amount-weighted opacity/material/base/top values
    # remain in the Rust output and are not replaced here.
    sampled_fields = _horizontal_sample_fields(
        prepared,
        samples,
        stencil,
        _NATIONAL_FRAME_SELECTED_COLUMN_FIELDS,
    )
    high = np.clip(sampled_fields[:, :, 0], 0.0, 1.0)
    mid = np.clip(sampled_fields[:, :, 1], 0.0, 1.0)
    low = np.clip(sampled_fields[:, :, 2], 0.0, 1.0)
    deep = np.clip(sampled_fields[:, :, 3], 0.0, 1.0)
    evidence = np.clip(sampled_fields[:, :, 4], 0.0, 1.0)
    observer_mid_blocker = np.clip(sampled_fields[:, :, 5], 0.0, 1.0)
    observer_low_blocker = np.clip(sampled_fields[:, :, 6], 0.0, 1.0)
    visibility_gate = valid_mask & (
        np.clip(output[:, :, 5], 0.0, 1.0) >= MIN_OBSERVER_VISIBILITY_FOR_SCORE
    )
    dominant_pressure_family_code = np.rint(
        np.nan_to_num(
            output[:, :, NATIONAL_DOMINANT_BURNABLE_PRESSURE_FAMILY_OFFSET],
            nan=0.0,
            posinf=0.0,
            neginf=0.0,
        )
    ).astype(np.uint8)
    dominant_pressure_family_code = np.where(
        np.isin(dominant_pressure_family_code, (1, 2, 3)),
        dominant_pressure_family_code,
        0,
    ).astype(np.uint8)
    dominant_base_asl_mean = np.nan_to_num(
        output[:, :, NATIONAL_DOMINANT_BURNABLE_BASE_ASL_MEAN_OFFSET],
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    ).astype(np.float32, copy=False)
    dominant_top_asl_mean = np.nan_to_num(
        output[:, :, NATIONAL_DOMINANT_BURNABLE_TOP_ASL_MEAN_OFFSET],
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    ).astype(np.float32, copy=False)
    dominant_target_terrain_asl_mean = np.nan_to_num(
        output[
            :, :, NATIONAL_DOMINANT_BURNABLE_TARGET_TERRAIN_ASL_MEAN_OFFSET
        ],
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    ).astype(np.float32, copy=False)
    no_dominant_family = dominant_pressure_family_code == 0
    dominant_base_asl_mean[no_dominant_family] = 0.0
    dominant_top_asl_mean[no_dominant_family] = 0.0
    dominant_target_terrain_asl_mean[no_dominant_family] = 0.0
    profile_bundle = _profile_diagnostic_bundle(prepared, samples, stencil)
    if profile_bundle is None:
        cloud_material_class_code = np.where(deep >= 0.62, 8, np.where(high >= mid, 1, 2)).astype(np.uint8)
        cloud_material_class_code[np.maximum.reduce((high, mid, low, evidence)) < 0.06] = 0
        profile_deep = deep
        vertical_support_count = np.full(
            (point_count, step_count), -1, dtype=np.int16
        )
        vertical_uncertainty_m = np.zeros(
            (point_count, step_count), dtype=np.float32
        )
        vertical_proxy_confidence = np.full(
            (point_count, step_count), 0.55, dtype=np.float32
        )
        vertical_proxy_source = "gfs-layer-aggregate"
    else:
        cloud_material_class_code = profile_bundle.cloud_material_class_code
        profile_deep = profile_bundle.deep
        vertical_support_count = profile_bundle.support_count
        vertical_uncertainty_m = profile_bundle.uncertainty_m
        vertical_proxy_confidence = profile_bundle.confidence
        vertical_proxy_source = (
            "gfs-pressure-profile-plus-average-boundary-envelope"
            if prepared.model_source == "gfs"
            else "pressure-level-profile"
        )
    diagnostic_deep = np.maximum(
        np.clip(profile_deep, 0.0, 1.0),
        np.clip(output[:, :, NATIONAL_TARGET_DEEP_RISK_OFFSET], 0.0, 1.0),
    )
    valid = valid_mask.astype(np.uint8)
    offsets = expected_offsets.astype(np.int64)
    instant_scoring_started = time.monotonic()
    instant_components = score_instant_observer_cloud_field_batch(
        output[:, :, NATIONAL_VIEW_AMOUNT_OFFSET],
        output[:, :, NATIONAL_BURN_AMOUNT_OFFSET],
        output[:, :, NATIONAL_BURN_LOAD_OFFSET],
        output[:, :, NATIONAL_MATERIAL_LOAD_OFFSET],
        output[:, :, NATIONAL_INCOMING_AEROSOL_LOAD_OFFSET],
        output[:, :, 4],
        samples[:, :, NATIONAL_SAMPLE_LOCAL_AOD],
        burnable_gate=visibility_gate,
        incoming_block_risk=output[:, :, NATIONAL_INCOMING_BLOCK_OFFSET],
    )
    local_instant_components = score_instant_observer_cloud_field_batch(
        output[:, :, NATIONAL_LOCAL_VIEW_OFFSET],
        output[:, :, NATIONAL_LOCAL_BURN_OFFSET],
        output[:, :, NATIONAL_LOCAL_LOAD_OFFSET],
        output[:, :, NATIONAL_LOCAL_MATERIAL_LOAD_OFFSET],
        output[:, :, NATIONAL_LOCAL_INCOMING_AEROSOL_LOAD_OFFSET],
        output[:, :, 4],
        samples[:, :, NATIONAL_SAMPLE_LOCAL_AOD],
        burnable_gate=visibility_gate,
    )
    instant_quality_exact = np.where(
        valid_mask,
        instant_components["index"],
        0.0,
    )
    instant_quality = instant_quality_exact.astype(np.float32)
    burnable_coverage = np.where(
        valid_mask,
        instant_components["burnable_cloud_coverage"],
        0.0,
    ).astype(np.float32)
    physically_burnable = (
        valid_mask & instant_components["physically_burnable"]
    ).astype(np.uint8)
    batched_event_scores = score_observer_cloud_field_batch(
        output[:, :, NATIONAL_VIEW_AMOUNT_OFFSET],
        output[:, :, NATIONAL_BURN_AMOUNT_OFFSET],
        output[:, :, NATIONAL_BURN_LOAD_OFFSET],
        output[:, :, NATIONAL_MATERIAL_LOAD_OFFSET],
        output[:, :, NATIONAL_INCOMING_AEROSOL_LOAD_OFFSET],
        output[:, :, NATIONAL_INCOMING_BLOCK_OFFSET],
        output[:, :, 5],
        output[:, :, 4],
        offsets,
        samples[:, :, NATIONAL_SAMPLE_LOCAL_AOD],
        valid_mask,
        analysis_radius_km=analysis_radius_km,
        local_view_amount=output[:, :, NATIONAL_LOCAL_VIEW_OFFSET],
        local_burnable_amount=output[:, :, NATIONAL_LOCAL_BURN_OFFSET],
        local_burnable_load=output[:, :, NATIONAL_LOCAL_LOAD_OFFSET],
        local_burnable_material_load=output[:, :, NATIONAL_LOCAL_MATERIAL_LOAD_OFFSET],
        local_burnable_incoming_aerosol_load=output[
            :, :, NATIONAL_LOCAL_INCOMING_AEROSOL_LOAD_OFFSET
        ],
        instant_components=instant_components,
        local_instant_components=local_instant_components,
    )
    instant_scoring_seconds = time.monotonic() - instant_scoring_started
    if progress_callback is not None:
        progress_callback({
            "stage": "cloud_field",
            "percent": 54,
            "message": (
                f"全量云场即时评分完成：{processed_samples} 样本，"
                f"{processed_samples / max(instant_scoring_seconds, 1e-9):.0f} 样本/秒"
            ),
            "completed": point_count,
            "total": point_count,
        })
    if progress_callback is not None:
        progress_callback({
            "stage": "frames",
            "percent": 55,
            "message": f"编码 {point_count} 个精准格点的评分核心与时间帧",
            "completed": 0,
            "total": point_count,
        })

    frames: list[dict[str, Any]] = []
    for step_index in range(step_count):
        frames.append({
            "selected_column_diagnostic_source": selected_column_diagnostic_source,
            "offset_minutes": int(offsets[step_index]),
            "valid": _encoded_raw_u8(valid[:, step_index]),
            "instant_quality_index": _encoded_u16(instant_quality[:, step_index], scale=0.001),
            "physically_burnable": _encoded_raw_u8(physically_burnable[:, step_index]),
            "burnable_cloud_coverage": _encoded_u8(burnable_coverage[:, step_index]),
            "forward_view_cloud_field_amount": _encoded_u8(
                output[:, step_index, NATIONAL_VIEW_AMOUNT_OFFSET]
            ),
            "forward_burnable_cloud_field_amount": _encoded_u8(
                output[:, step_index, NATIONAL_BURN_AMOUNT_OFFSET]
            ),
            "forward_candidate_cloud_field_amount": _encoded_u8(
                np.where(
                    valid_mask[:, step_index],
                    output[:, step_index, NATIONAL_VETO_CANDIDATE_OFFSET],
                    0.0,
                )
            ),
            "forward_target_eligible_cloud_field_amount": _encoded_u8(
                np.where(
                    valid_mask[:, step_index],
                    output[:, step_index, NATIONAL_VETO_TARGET_ELIGIBLE_OFFSET],
                    0.0,
                )
            ),
            "forward_solar_geometry_reachable_cloud_field_amount": _encoded_u8(
                np.where(
                    valid_mask[:, step_index],
                    output[:, step_index, NATIONAL_VETO_GEOMETRY_REACHABLE_OFFSET],
                    0.0,
                )
            ),
            "forward_external_cloud_path_transmitted_field_amount": _encoded_u8(
                np.where(
                    valid_mask[:, step_index],
                    output[:, step_index, NATIONAL_VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET],
                    0.0,
                )
            ),
            "forward_unoccluded_burn_potential_upper_bound": _encoded_u8(
                np.where(
                    valid_mask[:, step_index],
                    output[:, step_index, NATIONAL_VETO_UNOCCLUDED_BURN_UPPER_OFFSET],
                    0.0,
                )
            ),
            "forward_burnable_cloud_load": _encoded_u16(
                output[:, step_index, NATIONAL_BURN_LOAD_OFFSET], scale=0.01
            ),
            "forward_burnable_cloud_material_load": _encoded_u16(
                output[:, step_index, NATIONAL_MATERIAL_LOAD_OFFSET], scale=0.01
            ),
            "forward_burnable_cloud_incoming_aerosol_load": _encoded_u16(
                output[:, step_index, NATIONAL_INCOMING_AEROSOL_LOAD_OFFSET], scale=0.01
            ),
            "solar_incoming_block_risk": _encoded_u8(
                output[:, step_index, NATIONAL_INCOMING_BLOCK_OFFSET]
            ),
            "observer_local_cloud_field_amount": _encoded_u8(
                output[:, step_index, NATIONAL_LOCAL_VIEW_OFFSET]
            ),
            "observer_local_burnable_cloud_field_amount": _encoded_u8(
                output[:, step_index, NATIONAL_LOCAL_BURN_OFFSET]
            ),
            "observer_local_burnable_cloud_load": _encoded_u16(
                output[:, step_index, NATIONAL_LOCAL_LOAD_OFFSET], scale=0.01
            ),
            "observer_local_burnable_cloud_material_load": _encoded_u16(
                output[:, step_index, NATIONAL_LOCAL_MATERIAL_LOAD_OFFSET], scale=0.01
            ),
            "observer_local_burnable_cloud_incoming_aerosol_load": _encoded_u16(
                output[:, step_index, NATIONAL_LOCAL_INCOMING_AEROSOL_LOAD_OFFSET],
                scale=0.01,
            ),
            "forward_burnable_cloud_ratio": _encoded_u8(
                np.divide(
                    output[:, step_index, NATIONAL_BURN_AMOUNT_OFFSET],
                    np.maximum(output[:, step_index, NATIONAL_VIEW_AMOUNT_OFFSET], 1e-6),
                    out=np.zeros(point_count, dtype=np.float32),
                    where=output[:, step_index, NATIONAL_VIEW_AMOUNT_OFFSET] > 1e-6,
                )
            ),
            "burn_score": _encoded_u8(output[:, step_index, 1]),
            "intensity_score": _encoded_u8(output[:, step_index, 2]),
            "extent_score": _encoded_u8(output[:, step_index, 3]),
            "vividness_score": _encoded_u8(output[:, step_index, 4]),
            "high_cloud": _encoded_u8(high[:, step_index]),
            "mid_cloud": _encoded_u8(mid[:, step_index]),
            "low_cloud": _encoded_u8(low[:, step_index]),
            "high_burn": _encoded_u8(output[:, step_index, NATIONAL_HIGH_BURN_OFFSET]),
            "mid_burn": _encoded_u8(output[:, step_index, NATIONAL_MID_BURN_OFFSET]),
            "low_burn": _encoded_u8(output[:, step_index, NATIONAL_LOW_BURN_OFFSET]),
            "cloud_evidence": _encoded_u8(evidence[:, step_index]),
            "selected_column_cloud_material_class_code": _encoded_raw_u8(
                cloud_material_class_code[:, step_index]
            ),
            "convective_risk": _encoded_u8(
                np.clip(diagnostic_deep[:, step_index], 0.0, 1.0)
            ),
            "dominant_burnable_pressure_family_code": _encoded_raw_u8(
                dominant_pressure_family_code[:, step_index]
            ),
            "illumination_score": _encoded_u8(output[:, step_index, 40]),
            "path_clearance_score": _encoded_u8(output[:, step_index, 37]),
            "blocking_risk": _encoded_u8(output[:, step_index, 39]),
            "target_score": _encoded_u8(output[:, step_index, 11]),
            "dominant_burnable_cloud_base_asl_mean_m": _encoded_i16(
                dominant_base_asl_mean[:, step_index], scale=10.0
            ),
            "dominant_burnable_cloud_top_asl_mean_m": _encoded_i16(
                dominant_top_asl_mean[:, step_index], scale=10.0
            ),
            "dominant_burnable_target_terrain_asl_mean_m": _encoded_i16(
                dominant_target_terrain_asl_mean[:, step_index], scale=10.0
            ),
            "local_aod550": _encoded_u16(
                samples[:, step_index, 5],
                scale=0.001,
            ),
            "target_deck_connected": _encoded_u8(
                output[:, step_index, NATIONAL_TARGET_DECK_CONNECTED_OFFSET]
            ),
            "target_deck_attenuated_fraction": _encoded_u8(
                output[:, step_index, NATIONAL_TARGET_DECK_ATTENUATED_OFFSET]
            ),
            "target_deep_risk": _encoded_u8(
                output[:, step_index, NATIONAL_TARGET_DEEP_RISK_OFFSET]
            ),
            "cloud_optical_depth": _encoded_u16(
                output[:, step_index, NATIONAL_CLOUD_OPTICAL_DEPTH_OFFSET], scale=0.001
            ),
            "peak_blocking": _encoded_u8(
                output[:, step_index, NATIONAL_PEAK_BLOCKING_OFFSET]
            ),
            "clear_gap_count": _encoded_u16(
                output[:, step_index, NATIONAL_CLEAR_GAP_COUNT_OFFSET], scale=0.01
            ),
            "observer_return_optical_depth": _encoded_u16(
                output[:, step_index, NATIONAL_OBSERVER_RETURN_OPTICAL_DEPTH_OFFSET],
                scale=0.001,
            ),
            "observer_return_transmission": _encoded_u8(
                output[:, step_index, NATIONAL_OBSERVER_RETURN_TRANSMISSION_OFFSET]
            ),
            "observer_local_return_optical_depth": _encoded_u16(
                output[:, step_index, NATIONAL_LOCAL_RETURN_OPTICAL_DEPTH_OFFSET],
                scale=0.001,
            ),
            "observer_local_return_transmission": _encoded_u8(
                output[:, step_index, NATIONAL_LOCAL_RETURN_TRANSMISSION_OFFSET]
            ),
        })

    results: list[dict[str, Any]] = []
    point_core: dict[str, Any] | None = None
    for point_index, point in enumerate(points):
        valid_indices = np.flatnonzero(valid[point_index])
        burnable_indices = valid_indices[physically_burnable[point_index, valid_indices] > 0]
        best_index = (
            max(
                (int(index) for index in burnable_indices),
                key=lambda index: (
                    float(instant_quality[point_index, index]),
                    float(burnable_coverage[point_index, index]),
                    float(output[point_index, index, NATIONAL_BURN_LOAD_OFFSET]),
                ),
            )
            if burnable_indices.size
            else None
        )
        instant_scores = (
            instant_quality_exact[point_index, valid_indices]
            if valid_indices.size
            else np.empty(0)
        )
        peak_quality_index = (
            float(instant_quality[point_index, best_index])
            if best_index is not None
            else 0.0
        )
        peak_burn_score = peak_quality_index / 2.5
        quality = _fire_cloud_quality(
            output[point_index],
            samples[point_index],
            offsets,
            valid_indices,
            analysis_radius_km=analysis_radius_km,
            instant_components={
                name: component[point_index]
                for name, component in instant_components.items()
            },
            local_instant_components={
                name: component[point_index]
                for name, component in local_instant_components.items()
            },
            precomputed_score=batched_event_scores[point_index],
        )
        quality["components"].update(cloud_optics_metadata)
        # Publish the aggregate step count from the same f32-rounded coverage
        # the point core publishes: an f64 burn within one f32 ulp of the
        # event threshold could otherwise flip the f32 comparison the
        # point-core validator uses and reject a legal core by one step.
        threshold_f32 = np.float32(quality["components"]["event_active_cloud_threshold"])
        quality["components"]["physically_burnable_steps"] = float(
            np.count_nonzero(
                burnable_coverage[point_index, valid_indices] >= threshold_f32
            )
        )
        if model_source != "gfs":
            model_label = (
                "EC public TCC/RH proxy"
                if model_source == "ec"
                else "ECMWF IFS"
            )
            for component_name in (
                "quality_coverage_source",
                "observer_column_quality_source",
            ):
                source_text = quality["components"].get(component_name)
                if isinstance(source_text, str):
                    quality["components"][component_name] = source_text.replace(
                        "GFS", model_label
                    )
        for component_name in (
            "burnable_cloud_coverage_effective",
            "forward_view_cloud_field_amount_peak",
            "forward_burnable_cloud_field_amount_peak",
            "forward_burnable_cloud_load_peak",
            "forward_burnable_cloud_load_effective",
            "forward_burnable_cloud_material_load_peak",
            "forward_burnable_cloud_material_load_effective",
            "solar_incoming_block_risk_peak",
            "forward_burnable_cloud_ratio_peak",
            "duration_minutes",
        ):
            component_value = float(quality["components"][component_name])
            if not math.isfinite(component_value):
                raise ValueError(
                    f"current national quality component {component_name} is not finite"
                )
        representative_aod = _representative_quality_aod(quality)
        confidence_step_index = (
            best_index
            if best_index is not None
            else int(valid_indices[0]) if valid_indices.size else None
        )
        confidence = _national_confidence(
            aerosol_qualities[point_index],
            float(
                vertical_proxy_confidence[point_index, confidence_step_index]
            )
            if confidence_step_index is not None
            else 0.55,
        )
        forecast_lead_hours = (
            (float(evaluation_epoch[point_index, best_index]) - cycle_utc.timestamp()) / 3600.0
            if best_index is not None
            else None
        )
        timezone_name = str(point["timezone_name"])
        grid_steps: list[dict[str, Any]] = []
        for step_index in (range(step_count) if include_point_core else ()):
            is_valid = bool(valid[point_index, step_index])
            raw_values = output[point_index, step_index]
            values = (
                raw_values
                if is_valid
                else np.zeros(OUTPUT_FIELD_COUNT, dtype=output.dtype)
            )
            sample = samples[point_index, step_index]
            instant_index = float(instant_quality_exact[point_index, step_index])
            instant_level = fire_cloud_quality_level(instant_index)
            brightness_color = (
                float(instant_components["brightness_color"][point_index, step_index])
                if is_valid
                else 0.0
            )
            cloud_material = (
                float(instant_components["cloud_material"][point_index, step_index])
                if is_valid
                else 0.0
            )
            is_burnable = physically_burnable[point_index, step_index] > 0
            family_code = (
                int(dominant_pressure_family_code[point_index, step_index])
                if is_valid
                else 0
            )
            dominant_pressure_family = pressure_family_name(family_code)
            has_dominant_family = dominant_pressure_family is not None
            base_asl_mean_m = (
                float(dominant_base_asl_mean[point_index, step_index])
                if has_dominant_family
                else None
            )
            top_asl_mean_m = (
                float(dominant_top_asl_mean[point_index, step_index])
                if has_dominant_family
                else None
            )
            target_terrain_asl_mean_m = (
                float(dominant_target_terrain_asl_mean[point_index, step_index])
                if has_dominant_family
                else None
            )
            view_amount = float(np.clip(values[NATIONAL_VIEW_AMOUNT_OFFSET], 0.0, 1.0))
            burn_amount = float(np.clip(values[NATIONAL_BURN_AMOUNT_OFFSET], 0.0, 1.0))
            incoming_block = float(np.clip(values[NATIONAL_INCOMING_BLOCK_OFFSET], 0.0, 1.0))
            scene = (
                {
                    "selected_regime": "clear" if view_amount < 0.01 else (
                        "solar_incoming_blocked_field"
                        if incoming_block >= 0.55
                        and burn_amount < view_amount * 0.55
                        else "forward_cloud_field"
                    ),
                    "scene_scores": {
                        "clear": 1.0 - view_amount,
                        "forward_cloud_field": view_amount,
                        "burnable_cloud_field": burn_amount,
                        "solar_incoming_blocked_field": incoming_block,
                    },
                    "local_sky_score": 1.0 - incoming_block,
                    "remote_sky_score": burn_amount,
                    "convective_risk": float(
                        np.clip(
                            diagnostic_deep[point_index, step_index], 0.0, 1.0
                        )
                    ),
                }
                if is_valid
                else {
                    "selected_regime": "invalid",
                    "scene_scores": {
                        "clear": 0.0,
                        "forward_cloud_field": 0.0,
                        "burnable_cloud_field": 0.0,
                        "solar_incoming_blocked_field": 0.0,
                    },
                    "local_sky_score": 0.0,
                    "remote_sky_score": 0.0,
                    "convective_risk": 0.0,
                }
            )
            if not has_dominant_family:
                phase = None
            elif profile_bundle is not None and is_valid:
                phase = cloud_phase_diagnosis(
                    float(profile_bundle.ice[point_index, step_index]),
                    float(profile_bundle.liquid[point_index, step_index]),
                )
                phase["source"] = "gfs-selected-observer-column-condensate"
            else:
                phase = {
                    "label": "unknown",
                    "ice_fraction": None,
                    "confidence": 0.0,
                    "source": "pressure-profile-unavailable",
                }
            cloud_genus_proxy = cloud_genus_proxy_diagnosis(
                pressure_family=dominant_pressure_family,
                cloud_phase=phase,
                cloud_base_agl_m=(
                    max(base_asl_mean_m - target_terrain_asl_mean_m, 0.0)
                    if has_dominant_family
                    else None
                ),
                cloud_top_agl_m=(
                    max(top_asl_mean_m - target_terrain_asl_mean_m, 0.0)
                    if has_dominant_family
                    else None
                ),
                convective_risk=float(diagnostic_deep[point_index, step_index]),
                model_source=model_source,
            )
            color = color_diagnosis(
                quality_score=instant_index / 2.5,
                vividness_score=brightness_color,
                solar_elevation_deg=float(sample[0]),
                aod550=float(
                    instant_components["quality_aod550"][point_index, step_index]
                ),
                solar_incoming_illumination_score=(
                    float(
                        instant_components["incoming_aod_illumination_factor"][
                            point_index, step_index
                        ]
                    )
                    if is_valid
                    else 0.0
                ),
                deep_cloud_risk=(
                    float(diagnostic_deep[point_index, step_index])
                    if is_valid
                    else 0.0
                ),
                cloud_phase=str(phase["label"]) if phase is not None else "unknown",
            )
            grid_steps.append({
                "selected_column_diagnostic_source": selected_column_diagnostic_source,
                "offset_minutes": float(offsets[step_index]),
                "valid": is_valid,
                "instant_quality_index": instant_index,
                "instant_quality_level": instant_level,
                "physically_burnable": bool(is_burnable),
                "burnable_cloud_coverage": float(burnable_coverage[point_index, step_index]),
                "forward_view_cloud_field_amount": float(values[NATIONAL_VIEW_AMOUNT_OFFSET]),
                "forward_burnable_cloud_field_amount": float(
                    values[NATIONAL_BURN_AMOUNT_OFFSET]
                ),
                "forward_candidate_cloud_field_amount": float(
                    values[NATIONAL_VETO_CANDIDATE_OFFSET] if is_valid else 0.0
                ),
                "forward_target_eligible_cloud_field_amount": float(
                    values[NATIONAL_VETO_TARGET_ELIGIBLE_OFFSET] if is_valid else 0.0
                ),
                "forward_solar_geometry_reachable_cloud_field_amount": float(
                    values[NATIONAL_VETO_GEOMETRY_REACHABLE_OFFSET] if is_valid else 0.0
                ),
                "forward_external_cloud_path_transmitted_field_amount": float(
                    values[NATIONAL_VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET]
                    if is_valid
                    else 0.0
                ),
                "forward_unoccluded_burn_potential_upper_bound": float(
                    values[NATIONAL_VETO_UNOCCLUDED_BURN_UPPER_OFFSET]
                    if is_valid
                    else 0.0
                ),
                "forward_burnable_cloud_load": float(values[NATIONAL_BURN_LOAD_OFFSET]),
                "forward_burnable_cloud_material_load": float(
                    values[NATIONAL_MATERIAL_LOAD_OFFSET]
                ),
                "forward_burnable_cloud_incoming_aerosol_load": float(
                    values[NATIONAL_INCOMING_AEROSOL_LOAD_OFFSET]
                ),
                "solar_incoming_block_risk": float(values[NATIONAL_INCOMING_BLOCK_OFFSET]),
                "forward_high_cloud_amount": float(values[NATIONAL_HIGH_VIEW_OFFSET]),
                "forward_mid_cloud_amount": float(values[NATIONAL_MID_VIEW_OFFSET]),
                "forward_low_cloud_amount": float(values[NATIONAL_LOW_VIEW_OFFSET]),
                "forward_high_burnable_amount": float(values[NATIONAL_HIGH_BURN_OFFSET]),
                "forward_mid_burnable_amount": float(values[NATIONAL_MID_BURN_OFFSET]),
                "forward_low_burnable_amount": float(values[NATIONAL_LOW_BURN_OFFSET]),
                "observer_local_cloud_field_amount": float(values[NATIONAL_LOCAL_VIEW_OFFSET]),
                "observer_local_burnable_cloud_field_amount": float(values[NATIONAL_LOCAL_BURN_OFFSET]),
                "observer_local_burnable_cloud_load": float(values[NATIONAL_LOCAL_LOAD_OFFSET]),
                "observer_local_burnable_cloud_material_load": float(
                    values[NATIONAL_LOCAL_MATERIAL_LOAD_OFFSET]
                ),
                "observer_local_burnable_cloud_incoming_aerosol_load": float(
                    values[NATIONAL_LOCAL_INCOMING_AEROSOL_LOAD_OFFSET]
                ),
                "ground_area_cloud_field_amount": float(values[NATIONAL_GROUND_VIEW_OFFSET]),
                "ground_area_burnable_cloud_field_amount": float(values[NATIONAL_GROUND_BURN_OFFSET]),
                "ground_area_burnable_cloud_load": float(values[NATIONAL_GROUND_LOAD_OFFSET]),
                "solar_elevation_deg": float(sample[0]),
                "solar_azimuth_deg": float(sample[1]),
                "target_deck_connected": float(values[NATIONAL_TARGET_DECK_CONNECTED_OFFSET]),
                "target_deck_attenuated_fraction": float(
                    values[NATIONAL_TARGET_DECK_ATTENUATED_OFFSET]
                ),
                "target_deep_risk": float(values[NATIONAL_TARGET_DEEP_RISK_OFFSET]),
                "cloud_optical_depth": float(values[NATIONAL_CLOUD_OPTICAL_DEPTH_OFFSET]),
                "peak_blocking": float(values[NATIONAL_PEAK_BLOCKING_OFFSET]),
                "clear_gap_count": float(values[NATIONAL_CLEAR_GAP_COUNT_OFFSET]),
                "observer_return_optical_depth": float(
                    values[NATIONAL_OBSERVER_RETURN_OPTICAL_DEPTH_OFFSET]
                ),
                "observer_return_transmission": float(
                    values[NATIONAL_OBSERVER_RETURN_TRANSMISSION_OFFSET]
                ),
                "observer_local_return_optical_depth": float(
                    values[NATIONAL_LOCAL_RETURN_OPTICAL_DEPTH_OFFSET]
                ),
                "observer_local_return_transmission": float(
                    values[NATIONAL_LOCAL_RETURN_TRANSMISSION_OFFSET]
                ),
                "cloud_geometry_proxy_source": vertical_proxy_source,
                "cloud_geometry_support_count": (
                    int(vertical_support_count[point_index, step_index])
                    if is_valid
                    else -1
                ),
                "cloud_geometry_uncertainty_m": (
                    float(vertical_uncertainty_m[point_index, step_index])
                    if is_valid
                    else 0.0
                ),
                "cloud_geometry_confidence": (
                    float(vertical_proxy_confidence[point_index, step_index])
                    if is_valid
                    else 0.0
                ),
                "forward_burnable_cloud_ratio": float(
                    np.clip(
                        values[NATIONAL_BURN_AMOUNT_OFFSET]
                        / max(values[NATIONAL_VIEW_AMOUNT_OFFSET], 1e-6),
                        0.0,
                        1.0,
                    )
                ),
                "brightness_color": brightness_color,
                "cloud_material_score": cloud_material,
                "selected_column_cloud_material_class_code": (
                    int(cloud_material_class_code[point_index, step_index])
                    if is_valid
                    else 0
                ),
                "quality_aod550": float(
                    instant_components["quality_aod550"][point_index, step_index]
                ),
                "dominant_burnable_pressure_family": dominant_pressure_family,
                "dominant_burnable_cloud_base_asl_mean_m": base_asl_mean_m,
                "dominant_burnable_cloud_top_asl_mean_m": top_asl_mean_m,
                "dominant_burnable_target_terrain_asl_mean_m": (
                    target_terrain_asl_mean_m
                ),
                "burn_score": instant_index / 2.5,
                "intensity_score": float(np.clip(values[NATIONAL_BURN_LOAD_OFFSET] / 2.5, 0.0, 1.0)),
                "extent_score": view_amount,
                "vividness_score": brightness_color,
                "visibility_score": float(values[5]),
                "observer_mid_blocker_opacity": (
                    float(observer_mid_blocker[point_index, step_index])
                    if is_valid
                    else 0.0
                ),
                "observer_low_blocker_opacity": (
                    float(observer_low_blocker[point_index, step_index])
                    if is_valid
                    else 0.0
                ),
                "selected_regime": scene["selected_regime"],
                "scene_scores": scene["scene_scores"],
                "local_sky_score": scene["local_sky_score"],
                "remote_sky_score": scene["remote_sky_score"],
                "convective_risk": scene["convective_risk"],
                "color_diagnosis": color,
                "cloud_phase_proxy": phase,
                "cloud_genus_proxy": cloud_genus_proxy,
            })
        best_pressure_family = (
            pressure_family_name(
                int(dominant_pressure_family_code[point_index, best_index])
            )
            if best_index is not None
            else None
        )
        best_base_asl_mean_m = (
            float(dominant_base_asl_mean[point_index, best_index])
            if best_index is not None and best_pressure_family is not None
            else None
        )
        best_top_asl_mean_m = (
            float(dominant_top_asl_mean[point_index, best_index])
            if best_index is not None and best_pressure_family is not None
            else None
        )
        best_target_terrain_asl_mean_m = (
            float(dominant_target_terrain_asl_mean[point_index, best_index])
            if best_index is not None and best_pressure_family is not None
            else None
        )
        best_intensity_score = (
            float(np.clip(
                output[point_index, best_index, NATIONAL_BURN_LOAD_OFFSET] / 2.5,
                0.0,
                1.0,
            ))
            if best_index is not None
            else 0.0
        )
        best_extent_score = (
            float(np.clip(
                output[point_index, best_index, NATIONAL_VIEW_AMOUNT_OFFSET],
                0.0,
                1.0,
            ))
            if best_index is not None
            else 0.0
        )
        best_vividness_score = (
            float(instant_components["brightness_color"][point_index, best_index])
            if best_index is not None
            else 0.0
        )
        best_convective_risk = (
            float(np.clip(diagnostic_deep[point_index, best_index], 0.0, 1.0))
            if best_index is not None
            else 0.0
        )
        best_selected_phase = (
            grid_steps[best_index].get("cloud_phase_proxy")
            if best_index is not None and include_point_core
            else (
                {
                    **cloud_phase_diagnosis(
                        float(profile_bundle.ice[point_index, best_index]),
                        float(profile_bundle.liquid[point_index, best_index]),
                    ),
                    "source": "gfs-selected-observer-column-condensate",
                }
                if best_index is not None and profile_bundle is not None
                else {
                    "label": "unknown",
                    "ice_fraction": None,
                    "confidence": 0.0,
                    "source": "pressure-profile-unavailable",
                }
            )
        )
        best_color = color_diagnosis(
            quality_score=peak_burn_score,
            vividness_score=best_vividness_score,
            solar_elevation_deg=(
                float(samples[point_index, best_index, 0])
                if best_index is not None
                else 0.0
            ),
            aod550=(
                float(instant_components["quality_aod550"][point_index, best_index])
                if best_index is not None
                else 0.0
            ),
            solar_incoming_illumination_score=(
                float(instant_components["incoming_aod_illumination_factor"][point_index, best_index])
                if best_index is not None
                else 0.0
            ),
            deep_cloud_risk=best_convective_risk,
            cloud_phase=(
                str(best_selected_phase["label"])
                if best_selected_phase is not None
                else "unknown"
            ),
        )
        raw_observer_elevation_asl_m = point["elevation_m"]
        if (
            isinstance(raw_observer_elevation_asl_m, bool)
            or not isinstance(raw_observer_elevation_asl_m, Real)
        ):
            raise ValueError("current national point elevation_m must be a finite number")
        observer_elevation_asl_m = float(raw_observer_elevation_asl_m)
        if (
            not math.isfinite(observer_elevation_asl_m)
            or not -500.0 <= observer_elevation_asl_m <= 9000.0
        ):
            raise ValueError(
                "current national point elevation_m must be between -500 and 9000 metres"
            )
        best_base_agl_mean_m = (
            max(best_base_asl_mean_m - best_target_terrain_asl_mean_m, 0.0)
            if best_base_asl_mean_m is not None
            and best_target_terrain_asl_mean_m is not None
            else None
        )
        best_top_agl_mean_m = (
            max(best_top_asl_mean_m - best_target_terrain_asl_mean_m, 0.0)
            if best_top_asl_mean_m is not None
            and best_target_terrain_asl_mean_m is not None
            else None
        )
        best_base_above_observer_mean_m = (
            best_base_asl_mean_m - observer_elevation_asl_m
            if best_base_asl_mean_m is not None
            else None
        )
        best_top_above_observer_mean_m = (
            best_top_asl_mean_m - observer_elevation_asl_m
            if best_top_asl_mean_m is not None
            else None
        )
        best_phase = (
            {
                "label": "unknown",
                "ice_fraction": None,
                "confidence": 0.0,
                "source": "not-resolved-for-native-burnable-target",
            }
            if best_pressure_family is not None
            else None
        )
        best_genus_proxy = (
            grid_steps[best_index].get("cloud_genus_proxy")
            if best_index is not None and include_point_core
            else cloud_genus_proxy_diagnosis(
                pressure_family=best_pressure_family,
                cloud_phase=best_selected_phase,
                cloud_base_agl_m=best_base_agl_mean_m,
                cloud_top_agl_m=best_top_agl_mean_m,
                convective_risk=best_convective_risk,
                model_source=model_source,
            )
        )
        if include_point_core:
            point_core = {
            "version": NATIONAL_POINT_CORE_VERSION,
            "algorithm": NATIONAL_POINT_CORE_ALGORITHM,
            "target_date": target_date.isoformat(),
            "phenomenon": phenomenon,
            "model_source": model_source,
            "forecast_mode": "model",
            "point_refinement": refinement_mode,
            "analysis_radius_km": float(analysis_radius_km),
            "precision_mode": refinement_mode,
            "lat": float(point["lat"]),
            "lon": float(point["lon"]),
            "timezone_name": timezone_name,
            "observer_elevation_asl_m": observer_elevation_asl_m,
            "anchor_local": _iso_timestamp(float(anchor_epoch[point_index]), timezone_name),
            "cycle_time_utc": cycle_utc.isoformat(),
            "model_cycles": {model_source: cycle_utc.isoformat()},
            "confidence_score": confidence,
            "confidence_kind": NATIONAL_CONFIDENCE_KIND,
            "forecast_lead_hours": forecast_lead_hours,
            "forecast_lead_semantics": NATIONAL_FORECAST_LEAD_SEMANTICS,
            "quality_index_semantics": NATIONAL_QUALITY_INDEX_SEMANTICS,
            "cloud_optics": dict(cloud_optics_metadata),
            "cloud_geometry_proxy_source": vertical_proxy_source,
            "cloud_geometry_boundary_semantics": (
                "average-envelope-not-instantaneous"
                if prepared.model_source == "gfs" and prepared.layer_profiles is not None
                else "pressure-profile-proxy"
            ),
            "cloud_geometry_support_count": (
                int(vertical_support_count[point_index, best_index])
                if best_index is not None
                else -1
            ),
            "cloud_geometry_uncertainty_m": (
                float(vertical_uncertainty_m[point_index, best_index])
                if best_index is not None
                else 0.0
            ),
            "cloud_geometry_confidence": (
                float(vertical_proxy_confidence[point_index, best_index])
                if best_index is not None
                else 0.55
            ),
            "model_disagreement_score": 0.0,
            "model_weights": {model_source: 1.0},
            "quality_index": quality["index"],
            "quality_level": quality["level"],
            "peak_quality_index": peak_quality_index,
            "peak_quality_level": fire_cloud_quality_level(peak_quality_index),
            "overall_score": (
                float(np.mean(instant_scores)) / 2.5
                if instant_scores.size
                else 0.0
            ),
            "best_offset_minutes": (
                float(offsets[best_index]) if best_index is not None else None
            ),
            "quality_components": quality["components"],
            "burnable_cloud_coverage_effective": float(
                quality["components"]["burnable_cloud_coverage_effective"]
            ),
            "forward_view_cloud_field_amount_peak": float(
                quality["components"]["forward_view_cloud_field_amount_peak"]
            ),
            "forward_burnable_cloud_field_amount_peak": float(
                quality["components"]["forward_burnable_cloud_field_amount_peak"]
            ),
            "forward_burnable_cloud_load_peak": float(
                quality["components"]["forward_burnable_cloud_load_peak"]
            ),
            "forward_burnable_cloud_load_effective": float(
                quality["components"]["forward_burnable_cloud_load_effective"]
            ),
            "forward_burnable_cloud_material_load_peak": float(
                quality["components"]["forward_burnable_cloud_material_load_peak"]
            ),
            "forward_burnable_cloud_material_load_effective": float(
                quality["components"]["forward_burnable_cloud_material_load_effective"]
            ),
            "solar_incoming_block_risk_peak": float(
                quality["components"]["solar_incoming_block_risk_peak"]
            ),
            "forward_burnable_cloud_ratio_peak": float(
                quality["components"]["forward_burnable_cloud_ratio_peak"]
            ),
            "burnable_duration_minutes": float(
                quality["components"]["duration_minutes"]
            ),
            "dominant_burnable_pressure_family": best_pressure_family,
            "dominant_burnable_cloud_base_asl_mean_m": best_base_asl_mean_m,
            "dominant_burnable_cloud_top_asl_mean_m": best_top_asl_mean_m,
            "dominant_burnable_target_terrain_asl_mean_m": best_target_terrain_asl_mean_m,
            "convective_risk": best_convective_risk,
            "aerosol": {
                "aod550": representative_aod,
                "source": aerosol_sources[point_index],
                "quality": aerosol_qualities[point_index],
            },
            "steps": grid_steps,
            }
        results.append({
            "name": str(point["name"]),
            "lat": float(point["lat"]),
            "lon": float(point["lon"]),
            "observer_elevation_asl_m": observer_elevation_asl_m,
            "frame_index": point_index,
            "burn_score": peak_burn_score,
            "peak_quality_index": peak_quality_index,
            "peak_quality_level": fire_cloud_quality_level(peak_quality_index),
            "overall_score": (
                float(np.mean(instant_scores)) / 2.5
                if instant_scores.size
                else 0.0
            ),
            "intensity_score": best_intensity_score,
            "extent_score": best_extent_score,
            "vividness_score": best_vividness_score,
            "quality_index": quality["index"],
            "quality_level": quality["level"],
            "quality_components": quality["components"],
            "cloud_optics": dict(cloud_optics_metadata),
            "dominant_burnable_pressure_family": best_pressure_family,
            "dominant_burnable_cloud_base_asl_mean_m": best_base_asl_mean_m,
            "dominant_burnable_cloud_top_asl_mean_m": best_top_asl_mean_m,
            "dominant_burnable_target_terrain_asl_mean_m": best_target_terrain_asl_mean_m,
            "dominant_burnable_cloud_base_agl_mean_m": best_base_agl_mean_m,
            "dominant_burnable_cloud_top_agl_mean_m": best_top_agl_mean_m,
            "dominant_burnable_cloud_base_above_observer_mean_m": (
                best_base_above_observer_mean_m
            ),
            "dominant_burnable_cloud_top_above_observer_mean_m": (
                best_top_above_observer_mean_m
            ),
            "dominant_burnable_cloud_phase": best_phase,
            "cloud_phase_proxy": best_selected_phase,
            "color_diagnosis": best_color,
            "dominant_burnable_cloud_genus_diagnosis": cloud_genus_diagnosis(
                dominant_pressure_family=best_pressure_family,
                cloud_phase=best_phase,
                cloud_base_agl_m=best_base_agl_mean_m,
                cloud_top_agl_m=best_top_agl_mean_m,
                convective_risk=best_convective_risk,
                model_source=model_source,
            ),
            "cloud_genus_proxy": best_genus_proxy,
            "confidence_score": confidence,
            "confidence_kind": NATIONAL_CONFIDENCE_KIND,
            "forecast_lead_hours": forecast_lead_hours,
            "forecast_lead_semantics": NATIONAL_FORECAST_LEAD_SEMANTICS,
            "quality_index_semantics": NATIONAL_QUALITY_INDEX_SEMANTICS,
            "cloud_geometry_proxy_source": vertical_proxy_source,
            "cloud_geometry_boundary_semantics": (
                "average-envelope-not-instantaneous"
                if prepared.model_source == "gfs" and prepared.layer_profiles is not None
                else "pressure-profile-proxy"
            ),
            "cloud_geometry_support_count": (
                int(vertical_support_count[point_index, best_index])
                if best_index is not None
                else -1
            ),
            "cloud_geometry_uncertainty_m": (
                float(vertical_uncertainty_m[point_index, best_index])
                if best_index is not None
                else 0.0
            ),
            "cloud_geometry_confidence": (
                float(vertical_proxy_confidence[point_index, best_index])
                if best_index is not None
                else 0.55
            ),
            "best_time_local": (
                _iso_timestamp(float(evaluation_epoch[point_index, best_index]), timezone_name)
                if best_index is not None else None
            ),
            "anchor_local": _iso_timestamp(float(anchor_epoch[point_index]), timezone_name),
            "near_cloud_ratio": None,
            "far_cloud_ratio": None,
            "compute_backend": compute_backend,
            "point_refinement": refinement_mode,
            "high_cloud": float(high[point_index, best_index]) if best_index is not None else 0.0,
            "mid_cloud": float(mid[point_index, best_index]) if best_index is not None else 0.0,
            "low_cloud": float(low[point_index, best_index]) if best_index is not None else 0.0,
            "cloud_evidence": float(evidence[point_index, best_index]) if best_index is not None else 0.0,
            "selected_column_cloud_material_class_code": (
                int(cloud_material_class_code[point_index, best_index])
                if best_index is not None
                else 0
            ),
            "convective_risk": best_convective_risk,
            "illumination_score": float(output[point_index, best_index, 40]) if best_index is not None else 0.0,
            "path_clearance_score": float(output[point_index, best_index, 37]) if best_index is not None else 0.0,
            "blocking_risk": float(output[point_index, best_index, 39]) if best_index is not None else 0.0,
        })

    frame_total_seconds = time.monotonic() - frame_started
    assembly_seconds = max(0.0, frame_total_seconds - instant_scoring_seconds)
    point_rate = point_count / max(frame_total_seconds, 1e-9)
    sample_rate = processed_samples / max(frame_total_seconds, 1e-9)
    return results, point_core, {
        "version": NATIONAL_FRAME_VERSION,
        "encoding": "base64-quantized-row-major-v1",
        "selected_column_diagnostic_source": selected_column_diagnostic_source,
        "point_count": point_count,
        "height_scale_m": 10.0,
        "cloud_optics": dict(cloud_optics_metadata),
        "precision": {
            "mode": refinement_mode,
            "point_spacing_deg": point_spacing_deg,
            "model_grid_spacing_deg": float(prepared.spacing),
            "point_count": point_count,
            "processed_sample_count": processed_samples,
            "candidate_limit_per_sample": 0,
            "vertical_profiles": prepared.layer_profiles is not None,
            "full_point_coverage": refinement_mode == "raytraced-sky-v4-cpu",
            "aerosol_profile_mode": NATIONAL_AEROSOL_PROFILE_MODE,
        },
        "performance": {
            "instant_scoring_seconds": round(instant_scoring_seconds, 3),
            "assembly_seconds": round(assembly_seconds, 3),
            "total_seconds": round(frame_total_seconds, 3),
            "points_per_second": round(point_rate, 1),
            "samples_per_second": round(sample_rate, 1),
        },
        "frames": frames,
    }


def _bilinear_profile_values(
    profiles: np.ndarray,
    target_lats: np.ndarray,
    target_lons: np.ndarray,
    *,
    lat0: float,
    lon0: float,
    spacing: float,
    rows: int,
    cols: int,
    left_index: int,
    right_index: int,
    time_weight: float,
) -> np.ndarray:
    """Sample a pressure profile through the shared native-compatible sampler."""
    if profiles.ndim != 4 or profiles.shape[1] != rows * cols:
        raise ValueError("pressure profile shape does not match its grid metadata")
    stencil = _horizontal_stencil_for_coordinates(
        target_lats,
        target_lons,
        lat0=lat0,
        lon0=lon0,
        spacing=spacing,
        rows=rows,
        cols=cols,
    )
    sample = np.zeros((np.asarray(target_lats).size, NATIONAL_SAMPLE_FIELDS), dtype=np.float32)
    sample[:, 2] = np.float32(left_index)
    sample[:, 3] = np.float32(right_index)
    sample[:, 4] = np.float32(time_weight)
    return _horizontal_sample_tensor_step(profiles, sample, stencil)


def _profile_field_has_finite_support(
    profiles: np.ndarray,
    target_lats: np.ndarray,
    target_lons: np.ndarray,
    *,
    field_index: int,
    lat0: float,
    lon0: float,
    spacing: float,
    rows: int,
    cols: int,
    left_index: int,
    right_index: int,
) -> np.ndarray:
    """Report whether each sampled profile has any finite contributing value."""
    stencil = _horizontal_stencil_for_coordinates(
        target_lats,
        target_lons,
        lat0=lat0,
        lon0=lon0,
        spacing=spacing,
        rows=rows,
        cols=cols,
    )
    contributing = stencil.weights > 0.0
    first = np.asarray(
        profiles[left_index, stencil.cells, :, field_index],
        dtype=np.float32,
    )
    finite = np.any(np.isfinite(first), axis=2)
    if right_index != left_index:
        second = np.asarray(
            profiles[right_index, stencil.cells, :, field_index],
            dtype=np.float32,
        )
        finite |= np.any(np.isfinite(second), axis=2)
    return np.any(finite & contributing, axis=1)


def build_national_cross_section(
    profile_source: dict[str, Any],
    point: dict[str, Any],
    offset_minutes: float,
    *,
    max_distance_km: float = NATIONAL_ANALYSIS_RADIUS_KM,
    distance_step_km: float = 25.0,
) -> dict[str, Any]:
    profile_path = Path(str(profile_source["layer_profile_path"]))
    if not profile_path.is_file():
        raise FileNotFoundError("全国压力层缓存已过期，请重新运行全国计算。")
    loaded_profiles = np.load(profile_path, mmap_mode="r", allow_pickle=False)
    if isinstance(loaded_profiles, np.lib.npyio.NpzFile):
        with loaded_profiles:
            if "profiles" not in loaded_profiles.files:
                raise RuntimeError("全国压力层缓存没有 profiles")
            profiles = np.asarray(loaded_profiles["profiles"])
    else:
        profiles = loaded_profiles
    if profiles.ndim != 4 or profiles.shape[3] != LAYER_PROFILE_FIELD_COUNT:
        raise RuntimeError("全国压力层缓存版本过期，请重新运行全国计算。")
    pressure_levels = profile_source.get("pressure_levels")
    if not isinstance(pressure_levels, (list, tuple)) or len(pressure_levels) != profiles.shape[2]:
        raise RuntimeError("全国压力层缓存层序过期，请重新运行全国计算。")
    try:
        hours = np.asarray(profile_source["hours"], dtype=np.float64)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("全国压力层缓存时间轴无效，请重新运行全国计算。") from exc
    if (
        hours.ndim != 1
        or hours.size != profiles.shape[0]
        or hours.size == 0
        or np.any(~np.isfinite(hours))
        or np.any(np.diff(hours) <= 0.0)
    ):
        raise RuntimeError("全国压力层缓存时间轴无效，请重新运行全国计算。")
    target_date = date.fromisoformat(str(profile_source["target_date"]))
    phenomenon = str(profile_source["phenomenon"])
    cycle_utc = parse_utc_iso_datetime(
        profile_source["cycle_utc"], "national profile cycle_utc"
    )
    _, samples, evaluation_epoch, anchor_epoch = prepare_solar_samples(
        [point], target_date, phenomenon, cycle_utc, hours
    )
    if (
        isinstance(offset_minutes, bool)
        or not isinstance(offset_minutes, Real)
        or not math.isfinite(float(offset_minutes))
    ):
        raise ValueError("剖面相对时刻必须是有限数值。")
    offsets = (evaluation_epoch[0] - anchor_epoch[0]) / 60.0
    offset_matches = np.flatnonzero(
        np.isclose(
            offsets,
            float(offset_minutes),
            rtol=0.0,
            atol=1e-7,
        )
    )
    if offset_matches.size != 1:
        raise ValueError("剖面相对时刻必须精确匹配事件窗口的 5 分钟格点。")
    step_index = int(offset_matches[0])
    sample = samples[0, step_index]
    distances = np.arange(0.0, max_distance_km + 0.1, distance_step_km, dtype=np.float32)
    raw_observer_elevation = point["elevation_m"]
    if (
        isinstance(raw_observer_elevation, bool)
        or not isinstance(raw_observer_elevation, Real)
        or not math.isfinite(float(raw_observer_elevation))
        or not -500.0 <= float(raw_observer_elevation) <= 9000.0
    ):
        raise ValueError("剖面观测点海拔必须是 -500..9000 m 的有限数值。")
    observer = GeoPoint(
        float(point["lat"]),
        float(point["lon"]),
        float(raw_observer_elevation),
    )
    positions = [destination_point(observer, float(sample[1]), float(distance)) for distance in distances]
    lat0 = float(profile_source["lat0"])
    lon0 = float(profile_source["lon0"])
    spacing = float(profile_source["spacing"])
    rows = int(profile_source["rows"])
    cols = int(profile_source["cols"])
    raw_left = float(sample[2])
    raw_right = float(sample[3])
    weight = float(sample[4])
    if (
        not math.isfinite(raw_left)
        or not math.isfinite(raw_right)
        or raw_left != math.trunc(raw_left)
        or raw_right != math.trunc(raw_right)
    ):
        raise RuntimeError("全国压力层缓存时间索引无效，请重新运行全国计算。")
    left = int(raw_left)
    right = int(raw_right)
    if (
        left < 0
        or right < 0
        or left >= profiles.shape[0]
        or right >= profiles.shape[0]
        or right < left
        or right > left + 1
        or not math.isfinite(weight)
        or not 0.0 <= weight <= 1.0
        or (left == right and weight != 0.0)
    ):
        raise RuntimeError("全国压力层缓存时间插值无效，请重新运行全国计算。")
    values = _bilinear_profile_values(
        profiles,
        np.asarray([item.lat for item in positions], dtype=np.float64),
        np.asarray([item.lon for item in positions], dtype=np.float64),
        lat0=lat0,
        lon0=lon0,
        spacing=spacing,
        rows=rows,
        cols=cols,
        left_index=left,
        right_index=right,
        time_weight=weight,
    )
    terrain_support = _profile_field_has_finite_support(
        profiles,
        np.asarray([item.lat for item in positions], dtype=np.float64),
        np.asarray([item.lon for item in positions], dtype=np.float64),
        field_index=_PROFILE_SURFACE_ELEVATION,
        lat0=lat0,
        lon0=lon0,
        spacing=spacing,
        rows=rows,
        cols=cols,
        left_index=left,
        right_index=right,
    )

    def matrix(field: int, *, offset: float = 0.0, scale: float = 1.0) -> list[list[float | None]]:
        data = (values[:, :, field] + offset) * scale
        return [
            [round(float(value), 4) if math.isfinite(float(value)) else None for value in row]
            for row in data
        ]

    solar_elevation = float(sample[0])
    # The pressure profile is expressed as ASL geopotential height, and the
    # shadow boundary is a surface above mean sea level: a cloud base at MSL
    # height h is illuminated iff h >= R(sec(delta)-1). Anchoring the
    # threshold to per-point terrain shifted it by a full terrain height and
    # misjudged high-altitude decks.
    terrain_values = values[:, :, _PROFILE_SURFACE_ELEVATION]
    terrain_finite = np.isfinite(terrain_values)
    terrain_counts = np.sum(terrain_finite, axis=1)
    terrain_array = np.full(terrain_values.shape[0], np.nan, dtype=np.float64)
    has_terrain = terrain_counts > 0
    terrain_array[has_terrain] = (
        np.sum(np.where(terrain_finite, terrain_values, 0.0), axis=1)[has_terrain]
        / terrain_counts[has_terrain]
    )
    if terrain_array.size > 1 and (
        np.any(~terrain_support[1:])
        or np.any(~np.isfinite(terrain_array[1:]))
    ):
        raise RuntimeError("全国压力层剖面缺少远端地形，不能用观测点海拔代替。")
    # The first profile sample is the observer cell.  A user-supplied station
    # altitude may differ from the coarse GFS orography in that cell; keep the
    # origin physically consistent with the observer marker while retaining
    # model terrain for every remote path sample.
    if terrain_array.size:
        terrain_array[0] = float(observer.elevation_m)
    shadow_heights = np.asarray(
        [required_illumination_height_m(float(distance), solar_elevation) for distance in distances],
        dtype=np.float32,
    )
    layer_status = np.zeros(values.shape[:2], dtype=np.uint8)
    layer_base = np.full(values.shape[:2], np.nan, dtype=np.float32)
    layer_top = np.full(values.shape[:2], np.nan, dtype=np.float32)
    pressure_family_code = np.zeros(values.shape[:2], dtype=np.uint8)
    theoretical_targets: list[dict[str, Any]] = []
    model_source = str(profile_source.get("model_source") or "gfs")
    pressure_array = np.asarray(pressure_levels, dtype=np.float32)
    pressure_output_levels = [int(value) for value in pressure_levels]
    target_amount_array, _, _, _, _ = _effective_profile_cloud_fields(values)
    # Keep the profile diagnostic on the same resolved pressure-layer
    # boundary as the production scorer. A 400 hPa sample belongs to the
    # middle deck when a substantial 200/250/300 hPa upper deck is present;
    # otherwise it remains an isolated high-cloud sample.
    upper_support = np.max(
        np.where(
            pressure_array[None, :] <= _ISOLATED_UPPER_HIGH_MAX_PRESSURE_HPA,
            target_amount_array,
            0.0,
        ),
        axis=1,
    )
    lower_middle_support = np.max(
        np.where(
            (pressure_array[None, :] > _HIGH_CLOUD_MAX_PRESSURE_HPA)
            & (pressure_array[None, :] <= _MID_CLOUD_MAX_PRESSURE_HPA),
            target_amount_array,
            0.0,
        ),
        axis=1,
    )
    boundary_400_amount = np.max(
        np.where(
            pressure_array[None, :] == _HIGH_CLOUD_MAX_PRESSURE_HPA,
            target_amount_array,
            0.0,
        ),
        axis=1,
    )
    has_upper_pair = bool(
        np.any(pressure_array == 250.0) and np.any(pressure_array == 300.0)
    )
    upper_pair_support = np.min(
        np.where(
            (pressure_array[None, :] == 250.0)
            | (pressure_array[None, :] == 300.0),
            target_amount_array,
            1.0,
        ),
        axis=1,
    )
    if not has_upper_pair:
        upper_pair_support = np.zeros_like(boundary_400_amount)
    continuous_upper_400 = (
        (boundary_400_amount >= _CONTINUOUS_UPPER_400_MIN_AMOUNT)
        & (
            (lower_middle_support < _CONTINUOUS_UPPER_400_MAX_LOWER_MIDDLE_AMOUNT)
            | (upper_pair_support >= _CONTINUOUS_UPPER_400_MIN_UPPER_PAIR_AMOUNT)
        )
    )
    adaptive_middle_400 = (
        (pressure_array[None, :] == _HIGH_CLOUD_MAX_PRESSURE_HPA)
        & (upper_support[:, None] >= _ADAPTIVE_MIDDLE_400_UPPER_SUPPORT_THRESHOLD)
        & ~continuous_upper_400[:, None]
    )
    layer_groups = (
        ("high", "高云压力族", 1),
        ("middle", "中云压力族", 2),
        ("low", "低云压力族", 3),
    )
    height_array = np.nan_to_num(values[:, :, _PROFILE_HEIGHT], nan=np.nan)
    for distance_index, distance in enumerate(distances):
        for layer_name, layer_label, family_code in layer_groups:
            if layer_name == "high":
                pressure_mask = (
                    (pressure_array <= _HIGH_CLOUD_MAX_PRESSURE_HPA)
                    & ~adaptive_middle_400[distance_index]
                )
            elif layer_name == "middle":
                pressure_mask = (
                    ((pressure_array > _HIGH_CLOUD_MAX_PRESSURE_HPA)
                     & (pressure_array <= _MID_CLOUD_MAX_PRESSURE_HPA))
                    | adaptive_middle_400[distance_index]
                )
            else:
                pressure_mask = pressure_array > _MID_CLOUD_MAX_PRESSURE_HPA
            pressure_family_code[distance_index, pressure_mask] = family_code
            layer_indices = np.flatnonzero(pressure_mask).astype(np.int64)
            if layer_indices.size == 0:
                continue
            cloudy_indices = layer_indices[target_amount_array[distance_index, layer_indices] >= 0.06]
            if cloudy_indices.size == 0:
                continue
            base_m = float(np.nanmin(height_array[distance_index, cloudy_indices]))
            top_m = float(np.nanmax(height_array[distance_index, cloudy_indices]))
            aggregate_base_field, aggregate_top_field = {
                "high": (_PROFILE_HIGH_BASE, _PROFILE_HIGH_TOP),
                "middle": (_PROFILE_MIDDLE_BASE, _PROFILE_MIDDLE_TOP),
                "low": (_PROFILE_LOW_BASE, _PROFILE_LOW_TOP),
            }[layer_name]
            aggregate_base_values = values[
                distance_index, :, aggregate_base_field
            ]
            aggregate_top_values = values[
                distance_index, :, aggregate_top_field
            ]
            finite_base = aggregate_base_values[np.isfinite(aggregate_base_values)]
            finite_top = aggregate_top_values[np.isfinite(aggregate_top_values)]
            aggregate_base = float(np.mean(finite_base)) if finite_base.size else math.nan
            aggregate_top = float(np.mean(finite_top)) if finite_top.size else math.nan
            if (
                math.isfinite(aggregate_base)
                and math.isfinite(aggregate_top)
                and aggregate_top > aggregate_base
            ):
                # The aggregate fields are an envelope for the resolved
                # pressure-layer member, not an independent cloud layer. Old
                # profiles may still contain an envelope that spans an
                # adaptive 400 hPa boundary; reject it when it is separated
                # from the selected pressure samples by more than the
                # connected-deck tolerance used by the scorer.
                adaptive_boundary_active = (
                    layer_name == "high"
                    and bool(np.any(adaptive_middle_400[distance_index]))
                )
                aggregate_overlaps = (
                    not adaptive_boundary_active
                    or (
                        aggregate_base
                        >= base_m - _AGGREGATE_BOUNDARY_EXTENSION_TOLERANCE_M
                        and aggregate_top
                        <= top_m + _AGGREGATE_BOUNDARY_EXTENSION_TOLERANCE_M
                    )
                )
                if aggregate_overlaps:
                    base_m = min(base_m, aggregate_base)
                    top_m = max(top_m, aggregate_top)
            if not math.isfinite(base_m) or not math.isfinite(top_m):
                continue
            target_terrain_m = float(terrain_array[distance_index])
            illuminated = base_m >= float(shadow_heights[distance_index])
            # The profile is drawn in mean-sea-level heights, while the
            # observer may be on a mountain.  Use the spherical tangent
            # horizon for the actual observer radius; subtracting a flat
            # ``d²/(2R)`` drop from an observer-relative cloud height would
            # incorrectly treat a 4 km station as sea level.
            observer_relative_base_m = base_m - observer.elevation_m
            observer_relative_top_m = top_m - observer.elevation_m
            horizon_margin_m = spherical_horizon_margin_m(
                observer.elevation_m,
                base_m,
                float(distance),
            )
            # Strict burn definition: the illuminated cloud base itself must
            # remain above the geometric horizon; a high cloud top alone is
            # not enough to make a below-horizon base a burnable target.
            terrain_clearance_m = math.inf
            terrain_blocked = False
            if distance_index > 0 and math.isfinite(observer_relative_base_m):
                path_distances = distances[1 : distance_index + 1].astype(np.float64)
                path_rays = (
                    np.asarray(
                        [
                            spherical_line_of_sight_height_m(
                                observer.elevation_m,
                                base_m,
                                float(distance),
                                float(value),
                            )
                            for value in path_distances
                        ],
                        dtype=np.float64,
                    )
                )
                path_terrain = np.asarray(terrain_array[1 : distance_index + 1], dtype=np.float64)
                finite_path = np.isfinite(path_rays) & np.isfinite(path_terrain)
                if np.any(finite_path):
                    terrain_clearance_m = float(
                        np.min(path_rays[finite_path] - path_terrain[finite_path] - 25.0)
                    )
                    terrain_blocked = terrain_clearance_m <= 0.0
            visible = horizon_margin_m > 0.0 and not terrain_blocked
            # 1: cloudy but not a burn target; 2: lit but below the horizon;
            # 3: theoretically burnable and visible from the observer.
            status = 1 if not illuminated else 2 if not visible else 3
            layer_liquid = float(np.nansum(values[distance_index, cloudy_indices, _PROFILE_LIQUID]))
            layer_ice = float(np.nansum(values[distance_index, cloudy_indices, _PROFILE_ICE]))
            cloud_phase = _regional_cloud_phase_diagnosis(layer_ice, layer_liquid)
            if cloud_phase["label"] != "unknown":
                cloud_phase["source"] = (
                    f"{str(profile_source.get('model_source') or 'gfs')}-"
                    "pressure-level-condensate-same-distance-same-pressure-family"
                )
            if status == 3:
                status_reason = (
                    "云底高于理论照明阈值，且高于几何地平线；"
                    "此候选状态未评估云体遮挡"
                )
            elif not illuminated:
                status_reason = "云底未达到理论照明高度"
            elif horizon_margin_m <= 0.0 and terrain_blocked:
                status_reason = "已高于理论照明阈值，但低于几何地平线且回程地形阻挡"
            elif horizon_margin_m <= 0.0:
                status_reason = "已高于理论照明阈值，但低于几何地平线"
            elif terrain_blocked:
                status_reason = "已高于理论照明阈值，但回程视线被地形遮挡"
            else:
                status_reason = "已高于理论照明阈值，但回程几何视线不可见"
            layer_status[distance_index, cloudy_indices] = status
            layer_base[distance_index, cloudy_indices] = base_m
            layer_top[distance_index, cloudy_indices] = top_m
            theoretical_targets.append({
                "distance_km": float(distance),
                "pressure_family": layer_name,
                "pressure_family_label": layer_label,
                "cloud_phase": cloud_phase,
                "cloud_base_asl_m": base_m,
                "cloud_top_asl_m": top_m,
                "target_terrain_asl_m": target_terrain_m,
                "cloud_base_agl_m": max(base_m - target_terrain_m, 0.0),
                "cloud_top_agl_m": max(top_m - target_terrain_m, 0.0),
                "cloud_base_above_observer_m": observer_relative_base_m,
                "cloud_top_above_observer_m": observer_relative_top_m,
                "geometric_horizon_margin_m": horizon_margin_m,
                "observer_terrain_blocked": terrain_blocked,
                "observer_terrain_clearance_m": (
                    terrain_clearance_m if math.isfinite(terrain_clearance_m) else None
                ),
                "required_base_asl_m": float(shadow_heights[distance_index]),
                "required_base_agl_m": float(
                    shadow_heights[distance_index] - target_terrain_m
                ),
                "status": "burnable" if status == 3 else "illuminated_not_visible" if status == 2 else "not_burnable",
                "status_reason": status_reason,
            })
    return {
        "model_source": model_source,
        "terrain_source": profile_source.get(
            "terrain_source",
            None,
        ),
        "observer": {
            "lat": observer.lat,
            "lon": observer.lon,
            "elevation_asl_m": observer.elevation_m,
        },
        "offset_minutes": int(round(float(offsets[step_index]))),
        "valid_time_local": _iso_timestamp(float(evaluation_epoch[0, step_index]), str(point["timezone_name"])),
        "solar_elevation_deg": solar_elevation,
        "solar_azimuth_deg": float(sample[1]),
        "spatial_sampling": "bilinear-regular-grid",
        "analysis_radius_km": float(max_distance_km),
        "distances_km": [float(value) for value in distances],
        "terrain_elevation_asl_m": [
            round(float(value), 2) if math.isfinite(float(value)) else None
            for value in terrain_array
        ],
        "pressure_levels_hpa": pressure_output_levels,
        "required_illumination_base_asl_m": [
            float(value) for value in shadow_heights
        ],
        "required_illumination_base_agl_m": [
            round(float(value) - float(terrain), 2) if math.isfinite(float(value)) and math.isfinite(float(terrain)) else None
            for value, terrain in zip(shadow_heights, terrain_array)
        ],
        "theoretical_targets": theoretical_targets,
        "fields": {
            "geopotential_height_asl_m": matrix(_PROFILE_HEIGHT),
            "temperature_c": matrix(_PROFILE_TEMPERATURE, offset=-273.15),
            "rh_percent": matrix(_PROFILE_RH),
            "cloud_fraction_percent": matrix(_PROFILE_FRACTION),
            "cloud_presence": matrix(_PROFILE_PRESENCE),
            "cloud_amount": [
                [round(float(value), 4) if math.isfinite(float(value)) else None for value in row]
                for row in target_amount_array
            ],
            "cloud_liquid_kg_kg": matrix(_PROFILE_LIQUID),
            "cloud_ice_kg_kg": matrix(_PROFILE_ICE),
            "omega_pa_s": matrix(_PROFILE_OMEGA),
            "u_wind_ms": matrix(_PROFILE_U),
            "v_wind_ms": matrix(_PROFILE_V),
            "cloud_layer_status": [
                [int(value) if value > 0 else None for value in row]
                for row in layer_status
            ],
            "pressure_family_code": [
                [int(value) for value in row] for row in pressure_family_code
            ],
            "cloud_layer_base_asl_m": [
                [round(float(value), 2) if math.isfinite(float(value)) else None for value in row]
                for row in layer_base
            ],
            "cloud_layer_top_asl_m": [
                [round(float(value), 2) if math.isfinite(float(value)) else None for value in row]
                for row in layer_top
            ],
        },
    }
