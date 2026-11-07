"""ECMWF IFS basic single-point forecast path.

This module is intentionally separate from the GFS national path.  The public
IFS contract supplies pressure-level thermodynamics/wind, total cloud cover,
and surface geopotential.  TCC is carried as horizontal occupancy while the
pressure-level RH profile is an explicitly labelled vertical cloud proxy.  The
EC cloud path never imports GFS or observations; CAMS is an explicit aerosol
auxiliary and is never used to fill an EC cloud field.
"""

from __future__ import annotations

import math
import json
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from firesky.ecmwf_ifs import (
    ECMWF_IFS_BASIC_CONTRACT,
    ECMWF_IFS_FORECAST_CADENCE_HOURS,
    ECMWF_IFS_FORECAST_CADENCE_HIGH_HOURS,
    ECMWF_IFS_FORECAST_CADENCE_HIGH_START_HOUR,
    ECMWF_IFS_FORECAST_MAX_HOUR,
    ECMWF_IFS_PRESSURE_LEVELS,
    ECMWF_IFS_PUBLIC_LAYER_CLOUD_REASON,
    ECMWF_IFS_PUBLIC_UNSUPPORTED_REASON,
    ECMWF_IFS_RESOLUTION,
    EcmwfIfsAdapter,
    EcmwfIfsCapabilityError,
    EcmwfIfsInputTensor,
    load_ecmwf_ifs_grid_coordinates,
)
from firesky.ecmwf import EcmwfArtifactProbeError, ecmwf_artifact_available
from firesky.forecast_characteristics import (
    cloud_evolution_diagnosis,
    cloud_genus_diagnosis,
    score_curve_window,
)
from firesky.aerosol import cams_area_from_model_bbox, cams_profile_bracket_times
from firesky.geometry import (
    bounding_box,
    choose_cycle,
    haversine_km,
    initial_bearing_deg,
    solar_event_time_local,
)
from firesky.models import (
    AerosolReading,
    ForecastReport,
    ForecastRequest,
    GeoPoint,
    GridCellFeature,
    TimeStepAssessment,
)
from firesky.national_batch import (
    EC_SELECTED_COLUMN_DIAGNOSTIC_SOURCE,
    NATIONAL_ANALYSIS_RADIUS_KM,
    NATIONAL_SAMPLE_VALID,
    NationalBatchPrepared,
    _load_existing_external_derived_cache,
    _load_or_build_external_derived_cache,
    build_national_frame_payloads,
    prepare_solar_event_timeline,
    run_native_national_batch,
)
from firesky.scoring import clamp


ECMWF_IFS_BASIC_CLOUD_SEMANTICS = (
    "tcc-horizontal-occupancy; pressure-level-rh-conditional-proxy"
)
ECMWF_IFS_BASIC_PHASE_SEMANTICS = "unknown_without_model_level_clwc_ciwc"
ECMWF_IFS_BASIC_FORECAST_CADENCE_HOURS = ECMWF_IFS_FORECAST_CADENCE_HOURS
ECMWF_IFS_BASIC_DIAGNOSTIC_CONFIDENCE = 0.42
# Compatibility name for callers that already import the basic score constant.
ECMWF_IFS_BASIC_CONFIDENCE = ECMWF_IFS_BASIC_DIAGNOSTIC_CONFIDENCE
ECMWF_IFS_BASIC_CONFIDENCE_SEMANTICS = (
    "diagnostic_input_coverage_not_statistical_confidence"
)
ECMWF_IFS_BASIC_TIME_INTERPOLATION = (
    "linear_native_fields_with_native_wind_advection"
)
ECMWF_IFS_BASIC_ADVECTION = "native-family-wind-semilagrangian-from-EC-pl-u-v"
ECMWF_IFS_BASIC_AEROSOL_SEMANTICS = (
    "cams-aerext532-11level-vertical-profile-native-interpolation"
)
ECMWF_IFS_BASIC_AVAILABILITY_LAG_HOURS = 7
ECMWF_IFS_BASIC_NATIVE_SOLAR_TRACE_KM = 1000.0
ECMWF_IFS_BASIC_NATIVE_TRACE_HALO_KM = 400.0


@dataclass(frozen=True)
class EcmwfIfsBasicField:
    """EC-only feature field and source metadata for one valid time."""

    features: tuple[GridCellFeature, ...]
    cycle_utc: datetime
    valid_time_utc: datetime
    forecast_hour: int
    source_file: str
    terrain_elevation_m: float
    contract: str = ECMWF_IFS_BASIC_CONTRACT
    cloud_semantics: str = ECMWF_IFS_BASIC_CLOUD_SEMANTICS
    cloud_phase_semantics: str = ECMWF_IFS_BASIC_PHASE_SEMANTICS
    source_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class EcmwfIfsBasicPointForecast:
    """Report plus immutable provenance for the independent EC point run."""

    report: ForecastReport
    provenance: Mapping[str, Any]
    native_quality: Mapping[str, Any] | None = None


def choose_ecmwf_ifs_cycle(
    reference_utc: datetime,
    availability_lag_hours: int = ECMWF_IFS_BASIC_AVAILABILITY_LAG_HOURS,
    *,
    available_utc: datetime | None = None,
) -> datetime:
    """Choose an available 00/12Z IFS cycle without consulting another model.

    ``reference_utc`` is the event anchor used to avoid selecting a cycle that
    starts after a past event.  Automatic selection is capped at
    ``available_utc`` (or wall-clock UTC), so a future event cannot make the
    adapter request a not-yet-published cycle.  Explicit cycle overrides bypass
    this selector at the caller boundary.
    """

    if not isinstance(reference_utc, datetime) or reference_utc.tzinfo is None:
        raise ValueError("ECMWF cycle reference must be timezone-aware")
    reference = reference_utc.astimezone(timezone.utc)
    if available_utc is None:
        available = datetime.now(timezone.utc)
    elif not isinstance(available_utc, datetime) or available_utc.tzinfo is None:
        raise ValueError("ECMWF available time must be timezone-aware")
    else:
        available = available_utc.astimezone(timezone.utc)

    return choose_cycle(
        min(reference, available),
        (0, 12),
        int(availability_lag_hours),
    )


def _ecmwf_point_domain_geometry(radius_km: float) -> tuple[float, float, float]:
    """Return scoring candidate, native ray length, and full source radius.

    The native scorer traces a sunward path from every candidate out to
    ``min(1000 km, candidate + 400 km)``.  The EC source bbox therefore needs
    the candidate radius plus that complete path length; using only the
    candidate radius silently clips remote extinction at the domain boundary.
    """

    requested = float(radius_km)
    if not math.isfinite(requested) or requested <= 0.0:
        raise ValueError("EC point radius must be a finite positive number")
    candidate = min(requested, float(NATIONAL_ANALYSIS_RADIUS_KM))
    trace = min(
        ECMWF_IFS_BASIC_NATIVE_SOLAR_TRACE_KM,
        candidate + ECMWF_IFS_BASIC_NATIVE_TRACE_HALO_KM,
    )
    return candidate, trace, candidate + trace


def _ecmwf_source_bracket(
    cycle_utc: datetime,
    evaluation_time: datetime,
) -> tuple[int, int, float]:
    """Return the surrounding published IFS source times and weight.

    The public open-data product publishes 0-144 h every 3 h and 150-360 h
    every 6 h; brackets beyond f144 must snap to the 6 h grid or they would
    request steps that do not exist.
    """

    cycle = cycle_utc.astimezone(timezone.utc)
    target = evaluation_time.astimezone(timezone.utc)
    offset = (target - cycle).total_seconds() / 3600.0
    if offset < -1e-6:
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS cycle is later than a requested event evaluation time"
        )
    offset = max(0.0, offset)
    if offset <= ECMWF_IFS_FORECAST_CADENCE_HIGH_START_HOUR:
        cadence = ECMWF_IFS_BASIC_FORECAST_CADENCE_HOURS
        lower = int(math.floor(offset / cadence)) * cadence
        upper = int(math.ceil(offset / cadence)) * cadence
    else:
        cadence = ECMWF_IFS_FORECAST_CADENCE_HIGH_HOURS
        base = ECMWF_IFS_FORECAST_CADENCE_HIGH_START_HOUR
        lower = base + int(math.floor((offset - base) / cadence)) * cadence
        upper = base + int(math.ceil((offset - base) / cadence)) * cadence
        lower = min(lower, ECMWF_IFS_FORECAST_MAX_HOUR)
        upper = min(upper, ECMWF_IFS_FORECAST_MAX_HOUR)
    if upper == lower:
        return lower, lower, 0.0
    weight = (offset - lower) / float(upper - lower)
    return lower, upper, clamp(weight)


def ecmwf_source_bracket(
    cycle_utc: datetime,
    evaluation_time: datetime,
) -> tuple[int, int, float]:
    """Return the public IFS source-hour bracket for one evaluation time.

    This is a side-effect-free planning helper.  It does not inspect the
    cache or contact ECMWF.
    """

    return _ecmwf_source_bracket(cycle_utc, evaluation_time)


def _required_model_hours(
    cycle_utc: datetime,
    evaluation_times: Sequence[datetime],
) -> tuple[int, ...]:
    """Return all published IFS fields needed for every event-time bracket."""

    hours: set[int] = set()
    for evaluation_time in evaluation_times:
        lower, upper, _ = _ecmwf_source_bracket(cycle_utc, evaluation_time)
        hours.update((lower, upper))
    return tuple(sorted(hours))


def required_ecmwf_forecast_hours(
    cycle_utc: datetime,
    evaluation_times: Sequence[datetime],
) -> tuple[int, ...]:
    """Return unique public IFS source hours needed by event evaluations."""

    return _required_model_hours(cycle_utc, evaluation_times)


def choose_ecmwf_ifs_cycle_for_event(
    reference_utc: datetime,
    evaluation_times: Sequence[datetime],
    *,
    cache_dir: Path | str | None = None,
    available_utc: datetime | None = None,
    source: str = "ecmwf",
    timeout_seconds: float = 8.0,
    max_cycles: int = 8,
) -> datetime:
    """Choose the newest cycle containing every source hour for an event.

    A cycle can be visible before its late forecast steps have been published.
    Probe the highest uncached 3-hour bracket required by this event; ECMWF
    publishes a cycle's steps in ascending order, so this is sufficient while
    treating non-empty local bundles as already available.  Only a genuine
    missing artifact (404) permits trying the previous cycle; probe failures
    are surfaced to the caller.
    """
    if max_cycles < 1:
        raise ValueError("max_cycles must be positive")
    candidate = choose_ecmwf_ifs_cycle(
        reference_utc,
        available_utc=available_utc,
    )
    cache_root = Path(cache_dir) if cache_dir is not None else None
    for _ in range(max_cycles):
        hours = _required_model_hours(candidate, evaluation_times)
        complete = True
        missing_cached_hours: list[int] = []
        for hour in hours:
            if cache_root is not None:
                cached_path = EcmwfIfsAdapter.cache_path(cache_root, candidate, hour)
                try:
                    if cached_path.is_file() and cached_path.stat().st_size > 0:
                        continue
                except OSError:
                    pass
            missing_cached_hours.append(hour)
        if missing_cached_hours and not ecmwf_artifact_available(
            source,
            candidate,
            max(missing_cached_hours),
            timeout_seconds=timeout_seconds,
        ):
            # ECMWF publishes one cycle's forecast steps in ascending order;
            # an available maximum step implies its earlier brackets exist.
            complete = False
        if complete:
            return candidate
        candidate -= timedelta(hours=12)
    raise EcmwfArtifactProbeError(
        "ECMWF IFS has no complete cycle for the requested event brackets"
    )


def ecmwf_point_evaluation_times(request: ForecastRequest) -> tuple[datetime, ...]:
    """Return the event times used to plan an EC point source bracket."""
    point = {
        "name": request.name,
        "lat": float(request.point.lat),
        "lon": float(request.point.lon),
        "elevation_m": float(request.point.elevation_m),
        "timezone_name": request.timezone_name,
    }
    _, _, _, valid, evaluation_epoch, _ = prepare_solar_event_timeline(
        [point], request.target_date, request.phenomenon
    )
    if not np.any(valid[0]):
        raise EcmwfIfsCapabilityError("EC event has no valid solar evaluation times")
    return tuple(
        datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        for epoch in evaluation_epoch[0]
    )


def _interpolate_optional(
    left: float | None,
    right: float | None,
    weight: float,
) -> float | None:
    if left is None or right is None:
        if left is right:
            return left
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS source bracket has inconsistent optional fields"
        )
    return float(left + (right - left) * weight)


def _interpolate_basic_fields(
    left: EcmwfIfsBasicField,
    right: EcmwfIfsBasicField,
    *,
    valid_time_utc: datetime,
    forecast_hour: int,
    weight: float,
) -> EcmwfIfsBasicField:
    """Linearly interpolate aligned native EC fields.

    This compatibility helper only builds an event-time field.  The production
    EC path passes the resulting source tensors to the shared native scorer,
    which applies wind-aware cloud-family path sampling when EC PL ``u/v`` are
    available.
    """

    if len(left.features) != len(right.features):
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS source bracket has different native feature counts"
        )
    features: list[GridCellFeature] = []
    for left_feature, right_feature in zip(left.features, right.features):
        if (
            left_feature.pressure_hpa != right_feature.pressure_hpa
            or not math.isclose(left_feature.lat, right_feature.lat, abs_tol=1e-6)
            or not math.isclose(left_feature.lon, right_feature.lon, abs_tol=1e-6)
        ):
            raise EcmwfIfsCapabilityError(
                "ECMWF IFS source bracket does not share one native feature grid"
            )
        features.append(
            replace(
                left_feature,
                geopotential_height_m=left_feature.geopotential_height_m
                + (right_feature.geopotential_height_m - left_feature.geopotential_height_m)
                * weight,
                rh_percent=left_feature.rh_percent
                + (right_feature.rh_percent - left_feature.rh_percent) * weight,
                temperature_k=_interpolate_optional(
                    left_feature.temperature_k,
                    right_feature.temperature_k,
                    weight,
                ),
                omega_pa_s=_interpolate_optional(
                    left_feature.omega_pa_s,
                    right_feature.omega_pa_s,
                    weight,
                ),
                u_ms=_interpolate_optional(left_feature.u_ms, right_feature.u_ms, weight),
                v_ms=_interpolate_optional(left_feature.v_ms, right_feature.v_ms, weight),
                cloud_fraction_percent=_interpolate_optional(
                    left_feature.cloud_fraction_percent,
                    right_feature.cloud_fraction_percent,
                    weight,
                ),
                cloud_liquid_kg_kg=_interpolate_optional(
                    left_feature.cloud_liquid_kg_kg,
                    right_feature.cloud_liquid_kg_kg,
                    weight,
                ),
                cloud_ice_kg_kg=_interpolate_optional(
                    left_feature.cloud_ice_kg_kg,
                    right_feature.cloud_ice_kg_kg,
                    weight,
                ),
                horizontal_cloud_probability=_interpolate_optional(
                    left_feature.horizontal_cloud_probability,
                    right_feature.horizontal_cloud_probability,
                    weight,
                ),
                cloud_probability_given_cloud=_interpolate_optional(
                    left_feature.cloud_probability_given_cloud,
                    right_feature.cloud_probability_given_cloud,
                    weight,
                ),
            )
        )
    source_files = tuple(
        dict.fromkeys((*left.source_files, *right.source_files))
    )
    if not source_files:
        source_files = tuple(dict.fromkeys((left.source_file, right.source_file)))
    terrain = left.terrain_elevation_m + (
        right.terrain_elevation_m - left.terrain_elevation_m
    ) * weight
    return EcmwfIfsBasicField(
        features=tuple(features),
        cycle_utc=left.cycle_utc,
        valid_time_utc=valid_time_utc.astimezone(timezone.utc),
        forecast_hour=int(forecast_hour),
        source_file=left.source_file,
        terrain_elevation_m=float(terrain),
        source_files=source_files,
    )


def _rh_conditional_proxy(rh_percent: float) -> float:
    """Map PL RH to P(cloud at level | TCC footprint) for basic scoring only."""

    return clamp((float(rh_percent) - 45.0) / 45.0)


def basic_tensor_to_ec_field(
    tensor: EcmwfIfsInputTensor,
    observer: GeoPoint,
    *,
    radius_km: float,
) -> EcmwfIfsBasicField:
    """Convert one EC tensor to generic scoring features without source mixing."""

    if tensor.contract != ECMWF_IFS_BASIC_CONTRACT:
        raise EcmwfIfsCapabilityError(
            "EC point basic adapter requires the public-open-data/basic contract"
        )
    tcc = tensor.cloud_fraction_total_percent
    if tcc is None:
        raise EcmwfIfsCapabilityError("EC basic tensor has no TCC field")
    cell_count = int(tensor.latitudes.size)
    if tcc.shape != (cell_count,) or tensor.terrain_elevation_m.shape != (cell_count,):
        raise EcmwfIfsCapabilityError("EC basic tensor cell arrays have inconsistent shapes")
    if tensor.relative_humidity_percent.shape[1] != cell_count:
        raise EcmwfIfsCapabilityError("EC basic tensor pressure arrays have inconsistent shapes")

    cells: list[tuple[int, float, float, float, float]] = []
    for index in range(cell_count):
        point = GeoPoint(
            lat=float(tensor.latitudes[index]),
            lon=float(tensor.longitudes[index]),
        )
        distance = haversine_km(observer, point)
        if distance <= float(radius_km):
            cells.append(
                (
                    index,
                    point.lat,
                    point.lon,
                    distance,
                    initial_bearing_deg(observer, point),
                )
            )
    if not cells:
        raise EcmwfIfsCapabilityError(
            "EC basic tensor has no native cells inside the requested point radius"
        )

    pressure_count = len(tensor.pressure_levels_hpa)
    features: list[GridCellFeature] = []
    for index, lat, lon, distance, bearing in cells:
        horizontal_probability = clamp(float(tcc[index]) / 100.0)
        for pressure_index in range(pressure_count):
            pressure_hpa = int(tensor.pressure_levels_hpa[pressure_index])
            rh_percent = float(tensor.relative_humidity_percent[pressure_index, index])
            features.append(
                GridCellFeature(
                    lat=lat,
                    lon=lon,
                    distance_km=distance,
                    bearing_deg=bearing,
                    pressure_hpa=pressure_hpa,
                    geopotential_height_m=float(
                        tensor.geopotential_height_m[pressure_index, index]
                    ),
                    rh_percent=rh_percent,
                    temperature_k=float(tensor.temperature_k[pressure_index, index]),
                    omega_pa_s=float(tensor.omega_pa_s[pressure_index, index]),
                    u_ms=float(tensor.u_ms[pressure_index, index]),
                    v_ms=float(tensor.v_ms[pressure_index, index]),
                    # TCC is not a pressure-level cloud fraction. Keep the
                    # field empty and carry TCC in the horizontal channel.
                    cloud_fraction_percent=None,
                    cloud_liquid_kg_kg=None,
                    cloud_ice_kg_kg=None,
                    horizontal_cloud_probability=horizontal_probability,
                    cloud_probability_given_cloud=_rh_conditional_proxy(rh_percent),
                    surface_temperature_k=(
                        float(tensor.surface_temperature_k[index])
                        if tensor.surface_temperature_k is not None
                        else None
                    ),
                    surface_dewpoint_k=(
                        float(tensor.surface_dewpoint_k[index])
                        if tensor.surface_dewpoint_k is not None
                        else None
                    ),
                )
            )

    nearest_index = min(cells, key=lambda item: item[3])[0]
    terrain = float(tensor.terrain_elevation_m[nearest_index])
    if not math.isfinite(terrain):
        raise EcmwfIfsCapabilityError("EC basic tensor nearest terrain is not finite")
    return EcmwfIfsBasicField(
        features=tuple(features),
        cycle_utc=tensor.cycle_utc,
        valid_time_utc=tensor.valid_time_utc,
        forecast_hour=tensor.forecast_hour,
        source_file=tensor.source_file,
        terrain_elevation_m=terrain,
        source_files=(tensor.source_file,),
    )


def _step_characteristics(
    steps: Sequence[TimeStepAssessment],
    quality: Mapping[str, Any],
) -> list[dict[str, Any]]:
    quality_steps = quality.get("steps") or []
    result: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        quality_step = quality_steps[index] if index < len(quality_steps) else {}
        result.append(
            {
                "valid_time_local": step.valid_time_local.isoformat(),
                "burn_score": float(quality_step.get("instant_quality_index", 0.0)) / 2.5,
                "instant_quality_index": quality_step.get("instant_quality_index", 0.0),
                "physically_burnable": quality_step.get("physically_burnable", False),
                "burnable_cloud_coverage": quality_step.get("burnable_cloud_coverage", 0.0),
                "scene_scores": dict(step.scene_scores),
                "local_sky_score": step.local_sky_score,
                "remote_sky_score": step.remote_sky_score,
            }
        )
    return result


def _parse_cycle_override(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("EC cycle override must be an ISO-8601 UTC timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("EC cycle override must be aware UTC")
    return value.astimezone(timezone.utc)


def _tensor_to_native_grid(
    tensor: EcmwfIfsInputTensor,
) -> tuple[SimpleNamespace, np.ndarray, np.ndarray, float]:
    """Map one strict EC tensor to the shared native grid vocabulary.

    The native batch expects a regular row-major grid and pressure-level fields
    in the GFS-compatible names.  This function only renames/reorders EC data;
    the pressure-level TCC/RH cloud amount is an explicit EC-basic proxy.  No
    aggregate cloud layer or condensate value is synthesized.
    """

    if tensor.contract != ECMWF_IFS_BASIC_CONTRACT:
        raise EcmwfIfsCapabilityError(
            "EC native point path requires the public-open-data/basic contract"
        )
    pressure_levels = tuple(int(value) for value in tensor.pressure_levels_hpa)
    if pressure_levels != tuple(int(value) for value in ECMWF_IFS_PRESSURE_LEVELS):
        raise EcmwfIfsCapabilityError(
            "EC native point path requires the complete public IFS pressure-level set"
        )

    latitudes = np.asarray(tensor.latitudes, dtype=np.float64)
    longitudes = (
        np.asarray(tensor.longitudes, dtype=np.float64) + 180.0
    ) % 360.0 - 180.0
    if (
        latitudes.ndim != 1
        or longitudes.ndim != 1
        or latitudes.shape != longitudes.shape
        or latitudes.size == 0
        or np.any(~np.isfinite(latitudes))
        or np.any(~np.isfinite(longitudes))
    ):
        raise EcmwfIfsCapabilityError("EC native tensor coordinates are invalid")
    latitude_axis = np.unique(latitudes)
    longitude_axis = np.unique(longitudes)
    if latitude_axis.size * longitude_axis.size != latitudes.size:
        raise EcmwfIfsCapabilityError(
            "EC native point path requires a complete regular rectangular grid"
        )
    order = np.lexsort((longitudes, latitudes))
    ordered_latitudes = latitudes[order]
    ordered_longitudes = longitudes[order]
    expected_latitudes = np.repeat(latitude_axis, longitude_axis.size)
    expected_longitudes = np.tile(longitude_axis, latitude_axis.size)
    if not (
        np.allclose(ordered_latitudes, expected_latitudes, rtol=0.0, atol=1e-6)
        and np.allclose(ordered_longitudes, expected_longitudes, rtol=0.0, atol=1e-6)
    ):
        raise EcmwfIfsCapabilityError(
            "EC native point path found missing or duplicated regular-grid cells"
        )
    spacing_candidates: list[float] = []
    if latitude_axis.size > 1:
        latitude_spacing = np.diff(latitude_axis)
        if np.any(latitude_spacing <= 0.0) or not np.allclose(
            latitude_spacing,
            latitude_spacing[0],
            rtol=0.0,
            atol=1e-6,
        ):
            raise EcmwfIfsCapabilityError("EC latitude grid is not regularly spaced")
        spacing_candidates.append(float(latitude_spacing[0]))
    if longitude_axis.size > 1:
        longitude_spacing = np.diff(longitude_axis)
        if np.any(longitude_spacing <= 0.0) or not np.allclose(
            longitude_spacing,
            longitude_spacing[0],
            rtol=0.0,
            atol=1e-6,
        ):
            raise EcmwfIfsCapabilityError("EC longitude grid is not regularly spaced")
        spacing_candidates.append(float(longitude_spacing[0]))
    if not spacing_candidates:
        raise EcmwfIfsCapabilityError("EC native point path requires a two-dimensional grid")
    spacing = float(spacing_candidates[0])
    if any(not math.isclose(value, spacing, rel_tol=0.0, abs_tol=1e-4) for value in spacing_candidates[1:]):
        raise EcmwfIfsCapabilityError(
            "EC native point path requires equal latitude/longitude grid spacing"
        )

    cell_shape = (latitudes.size,)
    pressure_shape = (len(pressure_levels), latitudes.size)

    def finite_array(name: str, values: Any, shape: tuple[int, ...]) -> np.ndarray:
        array = np.asarray(values, dtype=np.float32)
        if array.shape != shape or np.any(~np.isfinite(array)):
            raise EcmwfIfsCapabilityError(
                f"EC native tensor {name} has an invalid shape or missing values"
            )
        return array

    tcc_values = tensor.cloud_fraction_total_percent
    if tcc_values is None:
        raise EcmwfIfsCapabilityError("EC native tensor has no public TCC field")
    tcc = finite_array("tcc", tcc_values, cell_shape)
    if np.any((tcc < 0.0) | (tcc > 100.0)):
        raise EcmwfIfsCapabilityError("EC native tensor TCC is outside 0..100 percent")
    terrain = finite_array("terrain", tensor.terrain_elevation_m, cell_shape)
    surface_geopotential = finite_array(
        "surface_geopotential", tensor.surface_geopotential_m2_s2, cell_shape
    )
    if np.any((terrain < -500.0) | (terrain > 9000.0)):
        raise EcmwfIfsCapabilityError("EC native tensor terrain is outside -500..9000 m")
    if not np.allclose(
        terrain,
        surface_geopotential / 9.80665,
        rtol=0.0,
        atol=2.0,
    ):
        raise EcmwfIfsCapabilityError(
            "EC native tensor terrain is inconsistent with surface geopotential"
        )

    pressure_values = {
        "gh": finite_array("gh", tensor.geopotential_height_m, pressure_shape),
        "r": finite_array("r", tensor.relative_humidity_percent, pressure_shape),
        "t": finite_array("t", tensor.temperature_k, pressure_shape),
        "w": finite_array("w", tensor.omega_pa_s, pressure_shape),
        "u": finite_array("u", tensor.u_ms, pressure_shape),
        "v": finite_array("v", tensor.v_ms, pressure_shape),
    }
    if np.any(
        (pressure_values["r"] < 0.0) | (pressure_values["r"] > 100.0)
    ):
        raise EcmwfIfsCapabilityError(
            "EC native tensor relative humidity is outside 0..100 percent"
        )

    ordered_tcc = tcc[order]
    ordered_terrain = terrain[order]
    fields: dict[tuple[str, int, str], np.ndarray] = {
        ("surface", 0, "orog"): np.ascontiguousarray(ordered_terrain),
        ("surface", 0, "z"): np.ascontiguousarray(surface_geopotential[order]),
        ("surface", 0, "tcc"): np.ascontiguousarray(ordered_tcc),
    }
    # Public IFS has no pressure-level cloud water/ice. Keep those channels
    # missing so the native material/phase logic cannot mistake unknown for 0.
    missing_condensate = np.full(cell_shape, np.nan, dtype=np.float32)
    for level_index, level in enumerate(pressure_levels):
        rh = pressure_values["r"][level_index, order]
        conditional_cloud = np.clip((rh - 45.0) / 45.0, 0.0, 1.0)
        proxy_fraction = np.clip(ordered_tcc * conditional_cloud, 0.0, 100.0)
        for name, values in pressure_values.items():
            fields[("isobaricInhPa", level, name)] = np.ascontiguousarray(
                values[level_index, order]
            )
        fields[("isobaricInhPa", level, "tcc")] = np.ascontiguousarray(
            proxy_fraction.astype(np.float32)
        )
        fields[("isobaricInhPa", level, "clwmr")] = missing_condensate.copy()
        fields[("isobaricInhPa", level, "icmr")] = missing_condensate.copy()

    grid = SimpleNamespace(
        latitudes=np.asarray(ordered_latitudes, dtype=np.float32),
        longitudes=np.asarray(ordered_longitudes, dtype=np.float32),
        fields=fields,
        raw_fields=dict(getattr(tensor, "raw_fields", {})),
        source_file=str(tensor.source_file),
        source_surface_geopotential_m2_s2=np.asarray(
            surface_geopotential[order], dtype=np.float32
        ),
        source_total_cloud_cover_percent=np.asarray(ordered_tcc, dtype=np.float32),
    )
    return (
        grid,
        np.asarray(latitude_axis, dtype=np.float32),
        np.asarray(longitude_axis, dtype=np.float32),
        spacing,
    )


def _native_source_times(
    cycle_utc: datetime,
    hours: Sequence[int],
    sample: np.ndarray,
) -> list[datetime]:
    """Resolve the actual 3-hour source fields behind one event sample."""

    if float(sample[NATIONAL_SAMPLE_VALID]) < 0.5:
        return []
    source_hours: list[int] = []
    for field_index in (2, 3):
        source_index = int(round(float(sample[field_index])))
        if source_index < 0 or source_index >= len(hours):
            raise EcmwfIfsCapabilityError(
                "EC native event sample references a source hour outside its tensor"
            )
        source_hour = int(hours[source_index])
        if source_hour not in source_hours:
            source_hours.append(source_hour)
    return [cycle_utc + timedelta(hours=hour) for hour in source_hours]


def _native_quality_from_point_core(
    point_core: Mapping[str, Any],
    expected_offsets: Sequence[int],
) -> dict[str, Any]:
    """Expose the point-core quality payload without re-scoring its steps."""

    steps = point_core.get("steps")
    if not isinstance(steps, list) or len(steps) != len(expected_offsets):
        raise EcmwfIfsCapabilityError(
            "EC native point core does not cover the canonical event timeline"
        )
    best_offset = point_core.get("best_offset_minutes")
    best_index = None
    if best_offset is not None:
        matches = [
            index
            for index, offset in enumerate(expected_offsets)
            if math.isclose(float(best_offset), float(offset), abs_tol=1e-6)
        ]
        if len(matches) != 1:
            raise EcmwfIfsCapabilityError(
                "EC native point core has an invalid best event offset"
            )
        best_index = matches[0]
    return {
        "quality_index": float(point_core["quality_index"]),
        "quality_level": str(point_core["quality_level"]),
        "quality_components": dict(point_core["quality_components"]),
        "duration_status": "estimated",
        "best_index": best_index,
        "steps": [dict(step) for step in steps],
    }


def _native_step_assessment(
    request: ForecastRequest,
    cycle_utc: datetime,
    evaluation_epoch: float,
    sample: np.ndarray,
    quality_step: Mapping[str, Any],
    hours: Sequence[int],
) -> TimeStepAssessment:
    valid_time_utc = datetime.fromtimestamp(float(evaluation_epoch), tz=timezone.utc)
    valid_time_local = valid_time_utc.astimezone(ZoneInfo(request.timezone_name))
    valid = bool(quality_step.get("valid", True))
    source_times = _native_source_times(cycle_utc, hours, sample)
    if str(quality_step.get("selected_column_diagnostic_source")) != EC_SELECTED_COLUMN_DIAGNOSTIC_SOURCE:
        raise EcmwfIfsCapabilityError(
            "EC native point core selected-column source is not the EC adapter"
        )
    physically_burnable = bool(quality_step.get("physically_burnable", False))
    reasons = [
        "云场来源：ECMWF IFS public Open Data basic。",
        "TCC 与 PL RH 组成 EC-basic 云柱代理；HCC/MCC/LCC、model-level CC/CLWC/CIWC 保持 unavailable。",
        "事件时刻使用左右 3 小时 EC 源场线性插值；shared native scorer "
        "对有 EC PL u/v 的云族执行 wind-aware 半拉格朗日路径采样。",
        "质量来自 shared native observer-field forward scorer；未调用 legacy assess_timestep。",
        "CAMS aerext532 11 层垂直 extinction profile 已由 native scorer 消费；缺失 profile 时 fail-closed。",
    ]
    if source_times:
        reasons.append(
            "EC 源时刻：" + ", ".join(
                value.strftime("%Y-%m-%d %H:%MZ") for value in source_times
            )
        )
    return TimeStepAssessment(
        forecast_hour=int(round((valid_time_utc - cycle_utc).total_seconds() / 3600.0)),
        valid_time_utc=valid_time_utc,
        valid_time_local=valid_time_local,
        solar_elevation_deg=float(quality_step.get("solar_elevation_deg", sample[0])),
        solar_azimuth_deg=float(quality_step.get("solar_azimuth_deg", sample[1])),
        burn_score=float(quality_step.get("burn_score", 0.0)),
        intensity_score=float(quality_step.get("intensity_score", 0.0)),
        extent_score=float(quality_step.get("extent_score", 0.0)),
        vividness_score=float(quality_step.get("vividness_score", 0.0)),
        visibility_score=float(quality_step.get("visibility_score", 0.0)),
        low_cloud_penalty=0.0,
        candidate_count=0,
        burning_cloud_count=1 if physically_burnable else 0,
        top_candidate=None,
        selected_regime=str(quality_step.get("selected_regime", "invalid" if not valid else "clear")),
        scene_scores=dict(quality_step.get("scene_scores") or {}),
        local_sky_score=float(quality_step.get("local_sky_score", 0.0)),
        remote_sky_score=float(quality_step.get("remote_sky_score", 0.0)),
        convective_risk=float(quality_step.get("convective_risk", 0.0)),
        color_diagnosis=dict(quality_step.get("color_diagnosis") or {}),
        cloud_source_times_utc=source_times,
        reasons=reasons,
        map_cells=[],
        geometric_cloud_coverage=float(
            quality_step.get("forward_view_cloud_field_amount", 0.0)
        ),
        structure_gate_score=float(quality_step.get("cloud_geometry_confidence", 0.0)),
        observation_burnable=True,
        forward_view_cloud_field_amount=float(
            quality_step.get("forward_view_cloud_field_amount", 0.0)
        ),
        forward_burnable_cloud_field_amount=float(
            quality_step.get("forward_burnable_cloud_field_amount", 0.0)
        ),
        observer_local_cloud_field_amount=float(
            quality_step.get("observer_local_cloud_field_amount", 0.0)
        ),
    )


def _dominant_burnable_from_point_core(
    point_core: dict,
    quality: dict,
    model_source: str,
) -> dict[str, Any]:
    """Extract the dominant-burnable geometry/genus block from the point core.

    与 GFS 单点公开字段对齐：EC 的 point_core 已含 dominant_burnable_* 与
    逐步 cloud_genus_proxy/cloud_phase_proxy，这里统一提取（agl/above_observer
    变体按 asl−terrain/observer 推导），供公开 payload 直接引用。
    """
    steps = point_core.get("steps") or []
    best_index = quality.get("best_index")
    best_step = (
        steps[int(best_index)]
        if best_index is not None and int(best_index) < len(steps)
        else (steps[0] if steps else {})
    )
    family = point_core.get("dominant_burnable_pressure_family")
    base_asl = point_core.get("dominant_burnable_cloud_base_asl_mean_m")
    top_asl = point_core.get("dominant_burnable_cloud_top_asl_mean_m")
    terrain = point_core.get("dominant_burnable_target_terrain_asl_mean_m")
    cloud_phase = best_step.get("cloud_phase_proxy")
    cloud_phase_label = (
        cloud_phase.get("label") if isinstance(cloud_phase, dict) else cloud_phase
    )
    genus_proxy = best_step.get("cloud_genus_proxy")
    phase_proxy = best_step.get("cloud_phase_proxy")
    convective_risk = float(point_core.get("convective_risk") or 0.0)
    genus_diagnosis = None
    if family is not None:
        genus_diagnosis = cloud_genus_diagnosis(
            dominant_pressure_family=family,
            cloud_phase=cloud_phase_label or "unknown",
            cloud_base_agl_m=(
                float(base_asl) - float(terrain)
                if base_asl is not None and terrain is not None
                else None
            ),
            cloud_top_agl_m=(
                float(top_asl) - float(terrain)
                if top_asl is not None and terrain is not None
                else None
            ),
            convective_risk=convective_risk,
            model_source=model_source,
        )
    return {
        "pressure_family": family,
        "cloud_phase": cloud_phase_label,
        "cloud_base_asl_mean_m": base_asl,
        "cloud_top_asl_mean_m": top_asl,
        "target_terrain_asl_mean_m": terrain,
        "cloud_base_agl_mean_m": (
            float(base_asl) - float(terrain)
            if base_asl is not None and terrain is not None
            else None
        ),
        "cloud_top_agl_mean_m": (
            float(top_asl) - float(terrain)
            if top_asl is not None and terrain is not None
            else None
        ),
        "cloud_base_above_observer_mean_m": (
            float(base_asl) - float(point_core.get("observer_elevation_asl_m"))
            if base_asl is not None
            and point_core.get("observer_elevation_asl_m") is not None
            else None
        ),
        "cloud_top_above_observer_mean_m": (
            float(top_asl) - float(point_core.get("observer_elevation_asl_m"))
            if top_asl is not None
            and point_core.get("observer_elevation_asl_m") is not None
            else None
        ),
        "genus_proxy": genus_proxy,
        "phase_proxy": phase_proxy,
        "genus_diagnosis": genus_diagnosis,
        "convective_risk": convective_risk,
    }


def run_ecmwf_basic_point_forecast(
    request: ForecastRequest,
    *,
    cache_dir: Path | str = ".cache/ecmwf",
    cams_cache_dir: Path | str = ".cache/cams",
    cycle_override: datetime | str | None = None,
    adapter: EcmwfIfsAdapter | None = None,
    aerosol_provider: Any | None = None,
    aerosol_loader: Callable[..., Any] | None = None,
    tensor_loader: Callable[[datetime, int, tuple[float, float, float, float]], EcmwfIfsInputTensor]
    | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> EcmwfIfsBasicPointForecast:
    """Run one EC-only point forecast through the shared native scorer.

    ``aerosol_provider`` and ``aerosol_loader`` remain named only to make an
    accidental migration from the old point path fail explicitly.  EC quality
    requires the production CAMS 11-level profile path owned by
    ``run_native_national_batch``; an AOD-only callback is never accepted.
    """

    started = time.perf_counter()
    if request.forecast_mode != "model" or request.model_source != "ec":
        raise ValueError("EC basic point forecast requires forecast_mode=model and model_source=ec")
    if aerosol_provider is not None or aerosol_loader is not None:
        raise EcmwfIfsCapabilityError(
            "EC native point scoring requires CAMS vertical profiles; "
            "aerosol_provider/aerosol_loader AOD callbacks are unsupported"
        )
    anchor_local = solar_event_time_local(
        request.target_date,
        request.point,
        request.timezone_name,
        request.phenomenon,
    )
    anchor_utc = anchor_local.astimezone(timezone.utc)
    point = {
        "name": request.name,
        "lat": float(request.point.lat),
        "lon": float(request.point.lon),
        "elevation_m": float(request.point.elevation_m),
        "timezone_name": request.timezone_name,
    }
    evaluation_times = list(ecmwf_point_evaluation_times(request))
    cycle = _parse_cycle_override(cycle_override)
    if cycle is None:
        cycle = choose_ecmwf_ifs_cycle_for_event(
            anchor_utc,
            evaluation_times,
            cache_dir=cache_dir,
        )
    cycle = cycle.replace(microsecond=0)
    _, _, _, valid, evaluation_epoch, anchor_epoch = prepare_solar_event_timeline(
        [point], request.target_date, request.phenomenon
    )
    hours = _required_model_hours(cycle, evaluation_times)
    candidate_radius_km, native_trace_radius_km, source_domain_radius_km = (
        _ecmwf_point_domain_geometry(request.radius_km)
    )
    bbox = bounding_box(request.point, source_domain_radius_km)
    source_adapter = adapter or EcmwfIfsAdapter(cache_dir=cache_dir, source="ecmwf")
    if source_adapter.resolution != ECMWF_IFS_RESOLUTION:
        raise ValueError("EC basic point forecast requires resolution 0p25")

    if tensor_loader is None:
        def load_tensor(
            cycle_value: datetime,
            hour: int,
            bbox_value: tuple[float, float, float, float],
        ) -> EcmwfIfsInputTensor:
            return source_adapter.load_tensor(cycle_value, hour, bbox=bbox_value)
    else:
        load_tensor = tensor_loader

    # A valid raw bundle and matching derived cache can be restored without
    # decoding any GRIB values. Coordinate arrays are read separately because
    # the historical derived grid stores fields only.
    cached_derived = None
    source_paths: list[str] = []
    source_metadata: list[dict[str, Any]] = []
    latitude_axis: np.ndarray | None = None
    longitude_axis: np.ndarray | None = None
    terrain_reference: np.ndarray | None = None
    pressure_levels: tuple[int, ...] = ()
    if tensor_loader is None and isinstance(source_adapter, EcmwfIfsAdapter):
        # The derived cache identity already includes the current raw-file
        # bytes.  Resolve the adapter's deterministic local paths first so a
        # strict prepared hit never enters the download/lock/network path.
        # ``load_tensor`` below remains the authoritative cold-cache fallback.
        candidate_paths = [
            EcmwfIfsAdapter.cache_path(source_adapter.cache_dir, cycle, hour)
            .expanduser()
            .resolve()
            for hour in hours
        ]
        candidate = _load_existing_external_derived_cache(
            "ec",
            cycle,
            hours,
            bbox,
            candidate_paths,
            expected_pressure_levels=ECMWF_IFS_PRESSURE_LEVELS,
        )
        if candidate is not None:
            coordinate_axes = [
                load_ecmwf_ifs_grid_coordinates(path, bbox=bbox)
                for path in candidate_paths
            ]
            latitude_axis, longitude_axis, spacing = coordinate_axes[0]
            if any(
                not (
                    np.array_equal(latitude_axis, current_latitude_axis)
                    and np.array_equal(longitude_axis, current_longitude_axis)
                    and math.isclose(spacing, current_spacing, rel_tol=0.0, abs_tol=1e-4)
                )
                for current_latitude_axis, current_longitude_axis, current_spacing
                in coordinate_axes[1:]
            ):
                raise EcmwfIfsCapabilityError(
                    "EC source hours do not share one native coordinate layout"
                )
            prepared_grid_candidate = candidate[0]
            if prepared_grid_candidate.shape[1] != latitude_axis.size * longitude_axis.size:
                raise EcmwfIfsCapabilityError(
                    "EC derived cache cell count does not match its coordinate layout"
                )
            terrain_reference = np.asarray(
                prepared_grid_candidate[0, :, 24], dtype=np.float32
            ).copy()
            if (
                terrain_reference.shape != (latitude_axis.size * longitude_axis.size,)
                or np.any(~np.isfinite(terrain_reference))
                or np.any((terrain_reference < -500.0) | (terrain_reference > 9000.0))
            ):
                raise EcmwfIfsCapabilityError(
                    "EC derived cache has invalid surface terrain"
                )
            cached_derived = candidate
            source_paths = [str(path) for path in candidate_paths]
            pressure_levels = candidate[2]
            source_metadata = [
                {
                    "file": source_path,
                    "valid_time_utc": (cycle + timedelta(hours=hour)).isoformat().replace("+00:00", "Z"),
                    "forecast_hour": int(hour),
                    "native_grid_shape": [
                        int(latitude_axis.size), int(longitude_axis.size)
                    ],
                    "selected_cell_count": int(latitude_axis.size * longitude_axis.size),
                    "surface_geopotential_units": "m2 s-2",
                    "terrain_units": "m",
                    "tcc_units": "%",
                    "model_level_cloud_fields": "unavailable_in_public_basic_contract",
                    "grid_spacing_deg": spacing,
                }
                for source_path, hour in zip(source_paths, hours)
            ]
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "tensor_ec",
                        "percent": 28,
                        "message": "复用 EC 严格同身份云柱张量，跳过 GRIB 值解码",
                    }
                )

    native_grids: list[Any] = []
    if cached_derived is None:
        for index, hour in enumerate(hours):
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "ecmwf",
                        "percent": 10 + int(25 * index / max(1, len(hours))),
                        "message": f"读取 ECMWF IFS basic f{hour:03d}",
                    }
                )
            tensor = load_tensor(cycle, hour, bbox)
            tensor_cycle = tensor.cycle_utc.astimezone(timezone.utc)
            if tensor_cycle != cycle or tensor.forecast_hour != hour:
                raise EcmwfIfsCapabilityError(
                    "EC basic tensor provenance does not match requested cycle/hour"
                )
            if tensor.valid_time_utc.astimezone(timezone.utc) != cycle + timedelta(hours=hour):
                raise EcmwfIfsCapabilityError(
                    "EC basic tensor valid time does not equal cycle plus forecast hour"
                )
            grid, current_latitude_axis, current_longitude_axis, spacing = _tensor_to_native_grid(tensor)
            if latitude_axis is None:
                latitude_axis = current_latitude_axis
                longitude_axis = current_longitude_axis
                terrain_reference = np.asarray(
                    grid.fields[("surface", 0, "orog")], dtype=np.float32
                ).copy()
            elif not (
                np.allclose(latitude_axis, current_latitude_axis, rtol=0.0, atol=1e-6)
                and np.allclose(longitude_axis, current_longitude_axis, rtol=0.0, atol=1e-6)
            ):
                raise EcmwfIfsCapabilityError(
                    "EC source hours do not share one native coordinate layout"
                )
            current_terrain = np.asarray(grid.fields[("surface", 0, "orog")], dtype=np.float32)
            if not np.allclose(terrain_reference, current_terrain, rtol=0.0, atol=2.0):
                raise EcmwfIfsCapabilityError(
                    "EC source hours do not share one surface terrain field"
                )
            native_grids.append(grid)
            source_path = str(Path(tensor.source_file).resolve())
            source_paths.append(source_path)
            source_metadata.append(
                {
                    "file": source_path,
                    "valid_time_utc": tensor.valid_time_utc.isoformat().replace("+00:00", "Z"),
                    "forecast_hour": int(hour),
                    "native_grid_shape": list(tensor.native_grid_shape),
                    "selected_cell_count": int(tensor.latitudes.size),
                    "surface_geopotential_units": "m2 s-2",
                    "terrain_units": "m",
                    "tcc_units": "%",
                    "model_level_cloud_fields": "unavailable_in_public_basic_contract",
                    "grid_spacing_deg": spacing,
                }
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "ecmwf",
                        "percent": 10 + int(25 * (index + 1) / max(1, len(hours))),
                        "message": f"读取并解码 ECMWF IFS basic f{hour:03d}",
                    }
                )

    if latitude_axis is None or longitude_axis is None or terrain_reference is None:
        raise EcmwfIfsCapabilityError("EC source tensor set is empty")
    if cached_derived is None:
        prepared_grid, profile_store, pressure_levels, profile_path = (
            _load_or_build_external_derived_cache(
                "ec",
                cycle,
                hours,
                bbox,
                native_grids,
                [Path(path) for path in source_paths],
                pressure_levels,
                progress_callback,
            )
        )
    else:
        prepared_grid, profile_store, pressure_levels, profile_path = cached_derived
    # CAMS application fills two reserved fields in-place. Durable cache grids
    # are read-only mmaps, so retain mmap loading while giving the scorer its
    # small mutable working copy.
    if not prepared_grid.flags.writeable:
        prepared_grid = np.array(prepared_grid, dtype=np.float32, order="C", copy=True)
    prepared = NationalBatchPrepared(
        grid=prepared_grid,
        hours=np.asarray(hours, dtype=np.float32),
        rows=int(latitude_axis.size),
        cols=int(longitude_axis.size),
        lat0=float(latitude_axis[0]),
        lon0=float(longitude_axis[0]),
        spacing=float(spacing),
        latitudes=np.asarray(np.repeat(latitude_axis, longitude_axis.size), dtype=np.float32),
        longitudes=np.asarray(np.tile(longitude_axis, latitude_axis.size), dtype=np.float32),
        cache_paths=list(source_paths),
        layer_profiles=profile_store,
        pressure_levels=pressure_levels,
        layer_profile_path=(
            str(profile_path)
            if profile_path is not None
            else "ec-public-basic-tcc-rh-proxy-native-profile"
        ),
        model_source="ec",
        surface_elevation_m=np.ascontiguousarray(terrain_reference, dtype=np.float32),
    )
    # A superset hit returns sliced tensors for scoring but keeps the original
    # profile path for the cross-section reader. Preserve that file's geometry
    # in provenance so cross-section indexing remains against the full mmap.
    profile_grid_latitude_axis = latitude_axis
    profile_grid_longitude_axis = longitude_axis
    if profile_path is not None:
        try:
            profile_metadata_path = profile_path.with_name(
                f"{profile_path.stem}.cache-v1.json"
            )
            profile_metadata = json.loads(
                profile_metadata_path.read_text(encoding="utf-8")
            )
            profile_coordinates = profile_metadata.get("coordinates")
            profile_latitudes = np.asarray(
                profile_coordinates["latitudes"], dtype=np.float32
            ).reshape(-1)
            profile_longitudes = np.asarray(
                profile_coordinates["longitudes"], dtype=np.float32
            ).reshape(-1)
            profile_grid_latitude_axis = np.unique(profile_latitudes)
            profile_grid_longitude_axis = np.unique(profile_longitudes)
            if (
                profile_grid_latitude_axis.size
                * profile_grid_longitude_axis.size
                != profile_latitudes.size
                or not np.array_equal(
                    profile_latitudes,
                    np.repeat(
                        profile_grid_latitude_axis,
                        profile_grid_longitude_axis.size,
                    ),
                )
                or not np.array_equal(
                    profile_longitudes,
                    np.tile(
                        profile_grid_longitude_axis,
                        profile_grid_latitude_axis.size,
                    ),
                )
            ):
                raise ValueError("EC profile cache coordinates are not rectangular")
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            profile_grid_latitude_axis = latitude_axis
            profile_grid_longitude_axis = longitude_axis
    effective_backend = "cpu" if request.compute_backend == "auto" else request.compute_backend
    cams_area = cams_area_from_model_bbox(
        (
            float(np.min(longitude_axis)),
            float(np.max(longitude_axis)),
            float(np.max(latitude_axis)),
            float(np.min(latitude_axis)),
        )
    )
    if progress_callback is not None:
        progress_callback(
            {
                "stage": "ecmwf",
                "percent": 38,
                "message": "使用 EC 云柱代理和 CAMS 垂直光学剖面执行 native observer-field 评分",
            }
        )
    input_prepare_seconds = time.perf_counter() - started
    native_started = time.perf_counter()
    (
        output,
        samples,
        evaluation_epoch_native,
        anchor_epoch_native,
        aerosol_sources,
        aerosol_qualities,
        aerosol_paths,
    ) = run_native_national_batch(
        prepared,
        [point],
        request.target_date,
        request.phenomenon,
        cycle,
        candidate_radius_km,
        Path(cams_cache_dir),
        cams_area,
        effective_backend,
    )
    native_compute_seconds = time.perf_counter() - native_started
    if len(aerosol_qualities) != 1 or str(aerosol_qualities[0]).lower() != "cams":
        raise RuntimeError(
            "EC native scoring requires CAMS aerosol quality; no fallback AOD is publishable"
        )
    if len(aerosol_sources) != 1 or not str(aerosol_sources[0]).strip():
        raise RuntimeError("EC native scoring did not return CAMS profile provenance")
    if not aerosol_paths:
        raise RuntimeError("EC native scoring did not return CAMS profile files")
    frames_started = time.perf_counter()
    results, point_core, frame_payload = build_national_frame_payloads(
        prepared,
        [point],
        request.target_date,
        request.phenomenon,
        cycle,
        output,
        samples,
        evaluation_epoch_native,
        anchor_epoch_native,
        aerosol_sources,
        aerosol_qualities,
        effective_backend,
        include_point_core=True,
        point_spacing_deg=0.0,
        analysis_radius_km=candidate_radius_km,
        progress_callback=progress_callback,
    )
    frame_assembly_seconds = time.perf_counter() - frames_started
    postprocess_started = time.perf_counter()
    if len(results) != 1 or point_core is None:
        raise RuntimeError("EC native scorer did not produce one point core")
    expected_offsets = tuple(
        int(round((float(epoch) - float(anchor_epoch_native[0])) / 60.0))
        for epoch in evaluation_epoch_native[0]
    )
    quality = _native_quality_from_point_core(point_core, expected_offsets)
    normalized = [
        _native_step_assessment(
            request,
            cycle,
            float(epoch),
            samples[0, index],
            quality["steps"][index],
            hours,
        )
        for index, epoch in enumerate(evaluation_epoch_native[0])
    ]
    best_index = quality.get("best_index")
    best = normalized[int(best_index)] if best_index is not None else None
    characteristics = _step_characteristics(normalized, quality)
    duration_value = quality.get("quality_components", {}).get("duration_minutes")
    duration = int(round(float(duration_value))) if duration_value is not None else None
    native_result = results[0]
    native_aerosol = point_core["aerosol"]
    native_aod = float(native_aerosol["aod550"])
    if not math.isfinite(native_aod) or native_aod < 0.0:
        raise RuntimeError("EC native scorer returned invalid CAMS AOD")
    event_aerosol = AerosolReading(
        aod550=native_aod,
        source=str(native_aerosol["source"]),
        quality="cams",
        observer_lat=request.point.lat,
        observer_lon=request.point.lon,
    )
    event_score = float(native_result["overall_score"])
    best_score = float(native_result["burn_score"])
    summary = [
        "ECMWF IFS basic 是独立云模式数值预测，不读取 GFS 或实况缓存。",
        f"EC cycle {cycle:%Y-%m-%d %H:%MZ}，评估 {len(normalized)} 个事件时刻。",
        "当前 public 合同使用 TCC + PL RH 云柱代理；HCC/MCC/LCC 和 model-level 云水/云冰不可用，结果属于 EC-basic proxy。",
        "EC surface z 按 geopotential m2/s2 / 9.80665 转为地形米；原始 z 和 native 网格保留在输入缓存/张量中。",
        "每个事件评估时刻都由 shared native scorer 使用对应 CAMS aerext532 11 层垂直 profile 的 3 小时 bracket；未找到完整 CAMS profile 会直接拒绝质量发布。",
        "quality_index 来自 native forward-field scorer；本路径未调用 legacy assess_timestep。",
        f"confidence_score={ECMWF_IFS_BASIC_DIAGNOSTIC_CONFIDENCE:.2f} 仅表示输入覆盖诊断，不是统计置信度。",
        *(best.reasons if best is not None else ["EC IFS 时间窗没有足够云目标。"]),
    ]
    report = ForecastReport(
        observer_name=request.name,
        phenomenon=request.phenomenon,
        model_source="ec",
        target_date=request.target_date,
        timezone_name=request.timezone_name,
        anchor_local=anchor_local,
        cycle_time_utc=cycle,
        model_cycles={"ec": cycle},
        aerosol=event_aerosol,
        overall_score=event_score,
        burn_score=best_score,
        intensity_score=float(native_result["intensity_score"]),
        extent_score=float(native_result["extent_score"]),
        vividness_score=float(native_result["vividness_score"]),
        duration_minutes=duration,
        potential_duration_minutes=duration,
        confidence_score=ECMWF_IFS_BASIC_DIAGNOSTIC_CONFIDENCE,
        best_time_local=best.valid_time_local if best is not None else None,
        summary=summary,
        steps=normalized,
        forecast_mode="model",
        model_disagreement_score=0.0,
        model_weights={"ec": 1.0},
        model_weight_samples={"ec": len(normalized)},
        model_component_scores={"ec": event_score},
        compute_backend_requested=request.compute_backend,
        compute_backend=effective_backend,
        selected_regime=best.selected_regime if best is not None else "unknown",
        scene_scores=dict(best.scene_scores) if best is not None else {},
        local_sky_score=best.local_sky_score if best is not None else 0.0,
        remote_sky_score=best.remote_sky_score if best is not None else 0.0,
        convective_risk=best.convective_risk if best is not None else 0.0,
        color_diagnosis=dict(best.color_diagnosis) if best is not None else {},
        cloud_evolution=cloud_evolution_diagnosis(
            characteristics,
            best_index=int(best_index) if best_index is not None else None,
        ),
        timing_window=score_curve_window(
            characteristics,
            regime=best.selected_regime if best is not None else "unknown",
        ),
    )
    finished = time.perf_counter()
    provenance = {
        "model_source": "ec",
        "model": "ifs",
        "member": source_adapter.member_name,
        "contract": ECMWF_IFS_BASIC_CONTRACT,
        "resolution": source_adapter.resolution,
        "source": source_adapter.source,
        "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
        "valid_times_utc": sorted(
            {
                source_time.isoformat().replace("+00:00", "Z")
                for step in normalized
                for source_time in step.cloud_source_times_utc
            }
        ),
        "evaluation_times_utc": [
            evaluation_time.isoformat().replace("+00:00", "Z")
            for evaluation_time in evaluation_times
        ],
        "forecast_hours": list(hours),
        "files": sorted(set(source_paths)),
        # The profile sidecar is the EC-only source for the optional point
        # cross-section endpoint.  Keeping its grid identity here lets the
        # endpoint re-open the already-built cache without downloading GFS or
        # rebuilding the EC tensors.
        "layer_profile_path": str(profile_path) if profile_path is not None else None,
        "pressure_levels": list(pressure_levels),
        "profile_grid": {
            "lat0": float(profile_grid_latitude_axis[0]),
            "lon0": float(profile_grid_longitude_axis[0]),
            "rows": int(profile_grid_latitude_axis.size),
            "cols": int(profile_grid_longitude_axis.size),
            "spacing": float(spacing),
        },
        "source_tensor_metadata": source_metadata,
        "native_core": "national_assess_batch+build_national_frame_payloads",
        "legacy_assess_timestep_used": False,
        "native_quality_index": float(quality["quality_index"]),
        "native_point_core_version": point_core["version"],
        "dominant_burnable": _dominant_burnable_from_point_core(
            point_core, quality, "ec"
        ),
        "native_frame_version": frame_payload["version"],
        "cloud_source_fields": {
            "total_cloud_cover": "EC surface TCC (%)",
            "vertical_cloud_proxy": "EC pressure-level RH conditioned by TCC",
            "cloud_liquid": "unavailable_in_public_basic_contract",
            "cloud_ice": "unavailable_in_public_basic_contract",
            "aggregate_layer_cloud": "unavailable_in_public_basic_contract",
        },
        "time_interpolation": ECMWF_IFS_BASIC_TIME_INTERPOLATION,
        "advection": ECMWF_IFS_BASIC_ADVECTION,
        "domain_geometry": {
            "candidate_radius_km": candidate_radius_km,
            "native_solar_trace_radius_km": native_trace_radius_km,
            "target_domain_radius_km": source_domain_radius_km,
            "target_grid_halo_semantics": (
                "candidate_plus_native_solar_trace; native cloud-family wind "
                "advection uses EC PL u/v"
            ),
        },
        "cams_aerosol_semantics": ECMWF_IFS_BASIC_AEROSOL_SEMANTICS,
        "cams_files": sorted(set(str(Path(path).resolve()) for path in aerosol_paths)),
        "cams_source_descriptions": list(aerosol_sources),
        "cams_valid_times_utc": [
            valid_time.isoformat().replace("+00:00", "Z")
            for valid_time in sorted(
                {
                    bracket_time
                    for hour in hours
                    for bracket_time in cams_profile_bracket_times(
                        cycle + timedelta(hours=hour)
                    )
                }
            )
        ],
        "cams_profile_validated": True,
        "cams_profile_consumed": True,
        "cams_profile_mode": "native-11level-vertical-extinction",
        "cloud_semantics": ECMWF_IFS_BASIC_CLOUD_SEMANTICS,
        "cloud_phase_semantics": ECMWF_IFS_BASIC_PHASE_SEMANTICS,
        "unsupported_reasons": [
            ECMWF_IFS_PUBLIC_UNSUPPORTED_REASON,
            ECMWF_IFS_PUBLIC_LAYER_CLOUD_REASON,
        ],
        "gfs_cache_used": False,
        "cams_cache_used": True,
        "observation_used": False,
        "confidence_score": ECMWF_IFS_BASIC_DIAGNOSTIC_CONFIDENCE,
        "confidence_semantics": ECMWF_IFS_BASIC_CONFIDENCE_SEMANTICS,
        "timing": {
            "input_prepare_seconds": round(float(input_prepare_seconds), 3),
            "native_compute_seconds": round(float(native_compute_seconds), 3),
            "frame_assembly_seconds": round(float(frame_assembly_seconds), 3),
            "postprocess_seconds": round(float(finished - postprocess_started), 3),
            "total_seconds": round(float(finished - started), 3),
        },
    }
    if progress_callback is not None:
        progress_callback({"stage": "ecmwf", "percent": 99, "message": "EC basic 单点评分完成"})
    return EcmwfIfsBasicPointForecast(
        report=report,
        provenance=provenance,
        native_quality=quality,
    )
