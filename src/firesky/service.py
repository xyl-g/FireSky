from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from firesky.aerosol import AerosolProvider, fetch_particulate_reading
from firesky.cycle_availability import query_latest_gfs_cycle
from firesky.ecmwf import choose_ecmwf_cycle
from firesky.geometry import solar_event_time_local, solar_snapshot
from firesky.forecast_characteristics import (
    cloud_evolution_diagnosis,
    score_curve_window,
)
from firesky.gfs import (
    GfsNomadsClient,
    LIGHTPATH_FETCH_RADIUS_KM,
    canonical_subset_bbox,
    decoded_grid_cache_path,
    features_from_decoded_grid,
    choose_gfs_cycle,
    vectorized_distance_and_bearing,
)
from firesky.geometry import angular_difference_deg, bounding_box
from firesky.version import NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION
from firesky.models import (
    AerosolReading,
    DomainSnapshot,
    ForecastReport,
    ForecastRequest,
    GeoPoint,
    GridCellFeature,
    NowcastAdjustment,
    TimeStepAssessment,
)
from firesky.native_core import (
    compute_backend_scope,
    native_gpu_available,
    normalize_compute_backend,
)
from firesky.forward_field_scoring import (
    MATERIAL_REFERENCE_QUALITY,
    MAX_SCORE as FIRE_CLOUD_QUALITY_MAX_SCORE,
)
from firesky.national_batch import fire_cloud_quality_from_assessments
from firesky.satellite import (
    JmaNowcastAnalyzer,
    MAX_NOWCAST_ADVECTION_HOURS,
    NOWCAST_CORE_WINDOW_MINUTES,
)
from firesky.satellite_cloud_column import VerticalPrior, vertical_prior_from_features
from firesky.scoring import assess_timestep as assess_satellite_timestep, clamp


# A Himawari scan is published over a ten-minute acquisition window.  A
# retrospective replay may therefore use the complete scan immediately after
# the event anchor; live requests remain causal and do not use this allowance.
HISTORICAL_HSD_LOOKAHEAD_MINUTES = 10.0
ENVIRONMENT_INPUTS_TIMEOUT_SECONDS = 300.0


def _native_gfs_prior_enabled() -> bool:
    configured = os.environ.get("FIRESKY_GFS_PRIOR_BACKEND", "rust")
    configured = configured.strip().lower()
    if configured not in {"python", "rust"}:
        raise ValueError("FIRESKY_GFS_PRIOR_BACKEND must be python or rust")
    return configured == "rust"


def _future_result_with_deadline(future, deadline: float):
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        raise TimeoutError("实况环境数据并行查询超时。")
    return future.result(timeout=remaining)


@dataclass(frozen=True)
class _SharedGfsNowcastDomain:
    """One decoded GFS domain shared by all satellite samples in a request."""

    grid: Any
    cache_path: str
    source_url: str
    bbox: tuple[float, float, float, float]


class GfsNowcastVerticalPriorProvider:
    """Strict same-cycle GFS prior provider for calibrated satellite columns."""

    def __init__(self, client: GfsNomadsClient, *, max_domains: int = 4) -> None:
        self.client = client
        if max_domains < 2:
            raise ValueError("GFS nowcast prior domain cache must retain at least two times")
        self._max_domains = int(max_domains)
        self._domains: OrderedDict[tuple[datetime, int, str], _SharedGfsNowcastDomain] = OrderedDict()
        self._lock = threading.Lock()

    def for_request(self, request: ForecastRequest) -> Callable[[GeoPoint, datetime], VerticalPrior]:
        def provider(point, valid_time):
            return self._get(request, point, valid_time)

        provider.batch = lambda points, valid_time: self._batch(request, points, valid_time)
        provider.native_retrieval_context = lambda valid_time: self.native_retrieval_context(
            request, valid_time
        )
        return provider

    def native_retrieval_context(
        self,
        request: ForecastRequest,
        valid_time: datetime,
    ) -> Optional[dict[str, Any]]:
        """Return cache-only inputs for the unified calibrated-retrieval RPC."""
        valid_time = valid_time.astimezone(timezone.utc)
        cycle = choose_model_cycle("gfs", valid_time, request.gfs_cycle_override)
        elapsed_hours = (valid_time - cycle).total_seconds() / 3600.0
        if elapsed_hours < -1e-6:
            raise ValueError("satellite time precedes selected GFS cycle")
        left_hour = max(0, int(math.floor(elapsed_hours)))
        right_hour = left_hour if abs(elapsed_hours - left_hour) < 1e-6 else left_hour + 1
        left = self._snapshot(request, cycle, left_hour)
        right = left if right_hour == left_hour else self._snapshot(request, cycle, right_hour)
        left_cache = decoded_grid_cache_path(Path(left.cache_path))
        right_cache = decoded_grid_cache_path(Path(right.cache_path))
        if not (left_cache / "manifest.json").is_file() or not (
            right_cache / "manifest.json"
        ).is_file():
            return None
        fraction = 0.0 if right is left else clamp(elapsed_hours - left_hour)
        grid_step_km = max(
            1.0, float(getattr(self.client, "_res_grid_deg", 0.25)) * 111.2
        )
        local_radius_km = min(
            max(float(request.radius_km), float(LIGHTPATH_FETCH_RADIUS_KM)),
            max(35.0, 2.0 * grid_step_km),
        )
        return {
            "cycle_time_utc": cycle,
            "left_forecast_hour": left_hour,
            "right_forecast_hour": right_hour,
            "fraction": fraction,
            "left_cache_path": left_cache,
            "right_cache_path": right_cache,
            "local_radius_km": local_radius_km,
            "domain_hash": hashlib.sha256(
                (left.source_url + "|" + right.source_url).encode()
            ).hexdigest()[:16],
            "resolution": self.client.resolution,
        }

    def _get(self, request: ForecastRequest, point: GeoPoint, valid_time: datetime) -> VerticalPrior:
        valid_time = valid_time.astimezone(timezone.utc)
        cycle = choose_model_cycle("gfs", valid_time, request.gfs_cycle_override)
        elapsed_hours = (valid_time - cycle).total_seconds() / 3600.0
        if elapsed_hours < -1e-6:
            raise ValueError("satellite time precedes selected GFS cycle")
        left_hour = max(0, int(math.floor(elapsed_hours)))
        right_hour = left_hour if abs(elapsed_hours - left_hour) < 1e-6 else left_hour + 1
        # Load one canonical domain for each model time.  HSD samples are
        # extracted from the decoded grid below; the sample point must never
        # become part of the network/download identity.
        left = self._snapshot(request, cycle, left_hour)
        right = left if right_hour == left_hour else self._snapshot(request, cycle, right_hour)
        fraction = 0.0 if right is left else clamp(elapsed_hours - left_hour)
        # Only the nearest model column is needed for a vertical prior.  A
        # grid-cell-sized local extraction keeps per-sample work bounded; the
        # full analysis radius is used only to define the shared downloaded
        # domain above.
        grid_step_km = max(1.0, float(getattr(self.client, "_res_grid_deg", 0.25)) * 111.2)
        local_radius_km = min(
            max(float(request.radius_km), float(LIGHTPATH_FETCH_RADIUS_KM)),
            max(35.0, 2.0 * grid_step_km),
        )
        left_features = features_from_decoded_grid(left.grid, point, local_radius_km)
        right_features = left_features if right is left else features_from_decoded_grid(right.grid, point, local_radius_km)
        features = _interpolate_vertical_prior_features(left_features, right_features, fraction)
        left_terrain = self._terrain_at(left, point)
        right_terrain = left_terrain if right is left else self._terrain_at(right, point)
        terrain = left_terrain + fraction * (right_terrain - left_terrain)
        identity = (
            f"gfs-prior:{cycle:%Y%m%d%H}:f{left_hour:03d}-{right_hour:03d}:"
            f"{self.client.resolution}:domain="
            f"{hashlib.sha256((left.source_url + '|' + right.source_url).encode()).hexdigest()[:16]}:"
            f"point={float(point.lat):+.5f},{float(point.lon):+.5f}"
        )
        if _native_gfs_prior_enabled():
            from firesky.rust_desktop_data import nowcast_gfs_prior

            return nowcast_gfs_prior(
                valid_time_utc=valid_time,
                cycle_time_utc=cycle,
                forecast_hour=left_hour,
                source="gfs-interpolated-vertical-prior",
                identity=identity,
                terrain_left_asl_m=left_terrain,
                terrain_right_asl_m=right_terrain,
                target_lat=float(point.lat),
                target_lon=float(point.lon),
                fraction=fraction,
                left_features=left_features,
                right_features=right_features,
            )
        return vertical_prior_from_features(
            valid_time_utc=valid_time,
            cycle_time_utc=cycle,
            forecast_hour=left_hour,
            source="gfs-interpolated-vertical-prior",
            identity=identity,
            terrain_asl_m=terrain,
            features=features,
            target_lat=float(point.lat),
            target_lon=float(point.lon),
        )

    def _batch(
        self,
        request: ForecastRequest,
        points: Sequence[GeoPoint],
        valid_time: datetime,
    ) -> list[Optional[VerticalPrior]]:
        if not points:
            return []
        if not _native_gfs_prior_enabled():
            result: list[Optional[VerticalPrior]] = []
            for point in points:
                try:
                    result.append(self._get(request, point, valid_time))
                except (RuntimeError, ValueError, KeyError, TypeError):
                    result.append(None)
            return result
        valid_time = valid_time.astimezone(timezone.utc)
        cycle = choose_model_cycle("gfs", valid_time, request.gfs_cycle_override)
        elapsed_hours = (valid_time - cycle).total_seconds() / 3600.0
        if elapsed_hours < -1e-6:
            raise ValueError("satellite time precedes selected GFS cycle")
        left_hour = max(0, int(math.floor(elapsed_hours)))
        right_hour = left_hour if abs(elapsed_hours - left_hour) < 1e-6 else left_hour + 1
        left = self._snapshot(request, cycle, left_hour)
        right = left if right_hour == left_hour else self._snapshot(request, cycle, right_hour)
        fraction = 0.0 if right is left else clamp(elapsed_hours - left_hour)
        grid_step_km = max(
            1.0, float(getattr(self.client, "_res_grid_deg", 0.25)) * 111.2
        )
        local_radius_km = min(
            max(float(request.radius_km), float(LIGHTPATH_FETCH_RADIUS_KM)),
            max(35.0, 2.0 * grid_step_km),
        )
        domain_hash = hashlib.sha256(
            (left.source_url + "|" + right.source_url).encode()
        ).hexdigest()[:16]
        # The decoded-grid contract is the measured fast path: it reads each
        # shared array once and avoids Python feature extraction per sample.
        # If it is unavailable, keep the established feature-array fallback for
        # older decoded caches, while preserving per-sample unknown semantics.
        decoded = self._decoded_batch(
            valid_time=valid_time,
            cycle=cycle,
            left_hour=left_hour,
            right_hour=right_hour,
            fraction=fraction,
            local_radius_km=local_radius_km,
            left=left,
            right=right,
            domain_hash=domain_hash,
            points=points,
        )
        if decoded is not None:
            return decoded
        return self._feature_batch(
            valid_time=valid_time,
            cycle=cycle,
            left_hour=left_hour,
            right_hour=right_hour,
            fraction=fraction,
            local_radius_km=local_radius_km,
            left=left,
            right=right,
            domain_hash=domain_hash,
            points=points,
        )

    def _feature_batch(
        self,
        *,
        valid_time: datetime,
        cycle: datetime,
        left_hour: int,
        right_hour: int,
        fraction: float,
        local_radius_km: float,
        left: _SharedGfsNowcastDomain,
        right: _SharedGfsNowcastDomain,
        domain_hash: str,
        points: Sequence[GeoPoint],
    ) -> list[Optional[VerticalPrior]]:
        """Fallback for caches that do not expose a usable decoded sidecar."""
        native_requests = []
        shared_items = []
        positions = []
        for index, point in enumerate(points):
            try:
                left_features = features_from_decoded_grid(left.grid, point, local_radius_km)
                right_features = (
                    left_features
                    if right is left
                    else features_from_decoded_grid(right.grid, point, local_radius_km)
                )
                left_terrain = self._terrain_at(left, point)
                right_terrain = left_terrain if right is left else self._terrain_at(right, point)
                identity = (
                    f"gfs-prior:{cycle:%Y%m%d%H}:f{left_hour:03d}-{right_hour:03d}:"
                    f"{self.client.resolution}:domain={domain_hash}:"
                    f"point={float(point.lat):+.5f},{float(point.lon):+.5f}"
                )
                native_requests.append(
                    {
                        "valid_time_utc": valid_time,
                        "cycle_time_utc": cycle,
                        "forecast_hour": left_hour,
                        "source": "gfs-interpolated-vertical-prior",
                        "identity": identity,
                        "terrain_left_asl_m": left_terrain,
                        "terrain_right_asl_m": right_terrain,
                        "target_lat": float(point.lat),
                        "target_lon": float(point.lon),
                        "fraction": fraction,
                        "left_features": left_features,
                        "right_features": right_features,
                    }
                )
                shared_items.append(
                    (
                        left_features,
                        right_features,
                        {
                            "identity": identity,
                            "terrain_left_asl_m": left_terrain,
                            "terrain_right_asl_m": right_terrain,
                            "target_lat": float(point.lat),
                            "target_lon": float(point.lon),
                        },
                    )
                )
                positions.append(index)
            except (RuntimeError, ValueError, KeyError, TypeError):
                continue
        result: list[Optional[VerticalPrior]] = [None] * len(points)
        if native_requests:
            from firesky.rust_desktop_data import (
                nowcast_gfs_prior_shared_batch,
                nowcast_gfs_priors,
            )

            def feature_key(feature: GridCellFeature) -> tuple[float, float, int]:
                return (
                    round(float(feature.lat), 5),
                    round(float(feature.lon), 5),
                    int(feature.pressure_hpa),
                )

            def old_batch() -> list[VerticalPrior]:
                result: list[VerticalPrior] = []
                chunk = []
                feature_count = 0
                for request_item in native_requests:
                    item_count = len(request_item["left_features"]) + len(
                        request_item["right_features"]
                    )
                    if chunk and (
                        len(chunk) >= 128 or feature_count + item_count > 4_000
                    ):
                        result.extend(nowcast_gfs_priors(chunk))
                        chunk = []
                        feature_count = 0
                    chunk.append(request_item)
                    feature_count += item_count
                if chunk:
                    result.extend(nowcast_gfs_priors(chunk))
                return result

            shared_chunks = []
            current_left: dict[tuple[float, float, int], GridCellFeature] = {}
            current_right: dict[tuple[float, float, int], GridCellFeature] = {}
            current_requests = []
            shared_possible = True
            for left_features, right_features, shared_request in shared_items:
                left_map = {feature_key(item): item for item in left_features}
                right_map = {feature_key(item): item for item in right_features}
                if (
                    left_map.keys() != right_map.keys()
                    or len(left_map) > 2_400
                    or len(right_map) > 2_400
                ):
                    shared_possible = False
                    break
                candidate_keys = set(current_left) | set(left_map)
                if current_requests and (
                    len(current_requests) >= 128 or len(candidate_keys) > 2_400
                ):
                    shared_chunks.append(
                        (
                            current_left,
                            current_right,
                            current_requests,
                        )
                    )
                    current_left = {}
                    current_right = {}
                    current_requests = []
                current_left.update(left_map)
                current_right.update(right_map)
                current_requests.append(shared_request)
            if current_requests:
                shared_chunks.append((current_left, current_right, current_requests))

            if shared_possible:
                native = []
                for left_map, right_map, requests_chunk in shared_chunks:
                    ordered_keys = sorted(left_map)
                    native.extend(
                        nowcast_gfs_prior_shared_batch(
                            valid_time_utc=valid_time,
                            cycle_time_utc=cycle,
                            forecast_hour=left_hour,
                            source="gfs-interpolated-vertical-prior",
                            fraction=fraction,
                            left_features=[left_map[key] for key in ordered_keys],
                            right_features=[right_map[key] for key in ordered_keys],
                            requests=requests_chunk,
                        )
                    )
            else:
                native = old_batch()
            for index, prior in zip(positions, native, strict=True):
                result[index] = prior
        return result

    def _decoded_batch(
        self,
        *,
        valid_time: datetime,
        cycle: datetime,
        left_hour: int,
        right_hour: int,
        fraction: float,
        local_radius_km: float,
        left: _SharedGfsNowcastDomain,
        right: _SharedGfsNowcastDomain,
        domain_hash: str,
        points: Sequence[GeoPoint],
    ) -> list[VerticalPrior] | None:
        """Use decoded-grid caches directly; return None for the legacy fallback."""
        left_path = Path(left.cache_path)
        right_path = Path(right.cache_path)
        # ``load_decoded_subset`` returns the raw GRIB path. The derived cache
        # directory is the sidecar consumed by the native reader.
        left_cache = decoded_grid_cache_path(left_path)
        right_cache = decoded_grid_cache_path(right_path)
        if not (left_cache / "manifest.json").is_file() or not (
            right_cache / "manifest.json"
        ).is_file():
            return None
        from firesky.rust_desktop_data import nowcast_gfs_prior_decoded_batch

        return list(
            nowcast_gfs_prior_decoded_batch(
                valid_time_utc=valid_time,
                cycle_time_utc=cycle,
                left_forecast_hour=left_hour,
                right_forecast_hour=right_hour,
                source="gfs-interpolated-vertical-prior",
                fraction=fraction,
                local_radius_km=local_radius_km,
                left_cache_path=left_cache,
                right_cache_path=right_cache,
                domain_hash=domain_hash,
                resolution=self.client.resolution,
                points=points,
            )
        )

    def _terrain_at(self, domain: _SharedGfsNowcastDomain, point: GeoPoint) -> float:
        values = domain.grid.fields.get(("surface", 0, "orog"))
        if values is None:
            raise RuntimeError("GFS vertical prior has no terrain field")
        distances, _ = vectorized_distance_and_bearing(
            point,
            np.asarray(domain.grid.latitudes, dtype=float),
            np.asarray(domain.grid.longitudes, dtype=float),
        )
        terrain_values = np.asarray(values, dtype=float)
        valid = np.isfinite(distances) & np.isfinite(terrain_values)
        if not np.any(valid):
            raise RuntimeError("GFS terrain field has no finite local samples")
        valid_indices = np.flatnonzero(valid)
        nearest = valid_indices[np.argsort(distances[valid_indices])[: min(4, valid_indices.size)]]
        local_distances = np.asarray(distances[nearest], dtype=float)
        local_values = terrain_values[nearest]
        exact = np.flatnonzero(local_distances <= 1e-6)
        if exact.size:
            terrain = float(local_values[int(exact[0])])
        else:
            weights = 1.0 / np.maximum(local_distances, 1e-3)
            terrain = float(np.sum(weights * local_values) / np.sum(weights))
        if not math.isfinite(terrain) or terrain < -500.0 or terrain > 9000.0:
            raise RuntimeError("GFS terrain field is invalid")
        return terrain

    def _snapshot(
        self,
        request: ForecastRequest,
        cycle: datetime,
        forecast_hour: int,
    ) -> _SharedGfsNowcastDomain:
        grid_step = float(getattr(self.client, "_res_grid_deg", 0.25))
        sampling_radius_km = max(float(request.radius_km), float(LIGHTPATH_FETCH_RADIUS_KM))
        # The nowcast prior only needs the satellite sampling footprint plus a
        # grid-sized interpolation margin.  A regional model client may still
        # carry a large fixed_bbox for ordinary model forecasts; never let that
        # domain widen the synchronous single-point prior request.
        interpolation_padding_km = max(35.0, 2.0 * grid_step * 111.2)
        requested_bbox = bounding_box(
            request.point,
            sampling_radius_km + interpolation_padding_km,
        )
        bbox = canonical_subset_bbox(requested_bbox, grid_step)
        source_url = self.client.build_subset_url(cycle, int(forecast_hour), bbox)
        domain_identity = json.dumps(
            {
                "resolution": self.client.resolution,
                "region": self.client.region,
                "bbox": [round(float(value), 4) for value in bbox],
                "source": hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:20],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        key = (cycle, int(forecast_hour), domain_identity)
        with self._lock:
            cached = self._domains.pop(key, None)
        if cached is not None:
            with self._lock:
                self._domains[key] = cached
            return cached
        try:
            # Synchronous nowcast scoring is cache-only.  Explicit prefetch
            # remains the only path allowed to download a missing subset.
            grid, cache_path = self.client.load_decoded_subset(
                cycle,
                int(forecast_hour),
                bbox,
                cache_only=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"GFS nowcast vertical prior cache is unavailable: "
                f"cycle {cycle:%Y-%m-%dT%HZ} f{int(forecast_hour):03d} "
                f"(resolution={self.client.resolution}, bbox={bbox}) 尚未预热。"
                "请先调用卫星缓存预热，或检查该周期是否已发布。"
            ) from exc
        domain = _SharedGfsNowcastDomain(
            grid=grid,
            cache_path=str(cache_path),
            source_url=source_url,
            bbox=tuple(float(value) for value in bbox),
        )
        with self._lock:
            existing = self._domains.pop(key, None)
            if existing is not None:
                self._domains[key] = existing
                return existing
            self._domains[key] = domain
            while len(self._domains) > self._max_domains:
                self._domains.popitem(last=False)
        return domain


def _interpolate_vertical_prior_features(
    left: Sequence[GridCellFeature],
    right: Sequence[GridCellFeature],
    fraction: float,
) -> list[GridCellFeature]:
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("GFS vertical prior interpolation fraction is invalid")
    if fraction <= 0.0:
        return list(left)
    if not right:
        raise RuntimeError("GFS vertical prior interpolation right domain is missing")
    right_map = {
        (round(float(item.lat), 5), round(float(item.lon), 5), int(item.pressure_hpa)): item
        for item in right
    }
    left_keys = {
        (round(float(item.lat), 5), round(float(item.lon), 5), int(item.pressure_hpa))
        for item in left
    }
    if left_keys != set(right_map):
        raise RuntimeError("GFS vertical prior interpolation domains do not match")
    result: list[GridCellFeature] = []
    for item in left:
        counterpart = right_map.get(
            (round(float(item.lat), 5), round(float(item.lon), 5), int(item.pressure_hpa))
        )
        if counterpart is None:
            raise RuntimeError("GFS vertical prior interpolation level is missing")
        updates: dict[str, Any] = {}
        for field_name in (
            "geopotential_height_m", "rh_percent", "temperature_k", "cloud_fraction_percent",
        ):
            left_value = getattr(item, field_name)
            right_value = getattr(counterpart, field_name)
            if (left_value is None) != (right_value is None):
                raise RuntimeError(
                    f"GFS vertical prior field {field_name} is missing on only one time side"
                )
            if left_value is not None:
                updates[field_name] = float(left_value) + fraction * (
                    float(right_value) - float(left_value)
                )
        result.append(replace(item, **updates))
    return result


def nowcast_event_uses_satellite_scoring(event_offset_minutes: float) -> bool:
    """Return whether HSD advection may score the requested event directly."""
    if not math.isfinite(float(event_offset_minutes)):
        raise ValueError("nowcast event offset must be finite")
    return abs(float(event_offset_minutes)) <= MAX_NOWCAST_ADVECTION_HOURS * 60.0


def nowcast_source_reference_time(reference_utc: datetime, anchor_utc: datetime) -> datetime:
    """Choose the observation lookup time without turning a replay into a forecast.

    ``reference_utc`` is normally the current request time.  When the target
    event is already in the past, using that current time asks the satellite
    provider for today's newest scan and makes the event appear to be a
    distant forecast.  Historical replay uses the event anchor as its starting
    point; the HSD lookup caller may add the bounded acquisition allowance below.
    """
    if reference_utc.tzinfo is None or anchor_utc.tzinfo is None:
        raise ValueError("nowcast reference and anchor must be timezone-aware")
    reference = reference_utc.astimezone(timezone.utc)
    anchor = anchor_utc.astimezone(timezone.utc)
    return anchor if reference > anchor else reference


def satellite_lookup_reference_time(reference_utc: datetime, anchor_utc: datetime) -> datetime:
    """Return the HSD lookup cutoff for live or retrospective analysis."""
    if reference_utc.tzinfo is None or anchor_utc.tzinfo is None:
        raise ValueError("nowcast reference and anchor must be timezone-aware")
    reference = reference_utc.astimezone(timezone.utc)
    anchor = anchor_utc.astimezone(timezone.utc)
    source = nowcast_source_reference_time(reference, anchor)
    if reference <= anchor:
        return source
    # A ten-minute Himawari acquisition can finish just after the target. The
    # allowance is retrospective-only; live requests remain causal.
    return source + timedelta(minutes=HISTORICAL_HSD_LOOKAHEAD_MINUTES)


def resolve_nowcast_gfs_cycle(
    request: ForecastRequest,
    anchor_utc: datetime,
    *,
    reference_utc: datetime | None = None,
) -> datetime:
    """Resolve one GFS cycle for the whole nowcast request.

    A future event must use the newest cycle available when the request was
    made; selecting from each future sample time can choose a cycle that has
    not been published yet. Historical replay is anchored to the event so it
    does not drift to the current operational cycle. Live requests probe
    NOMADS for the actually published cycle (bounded TTL cache) so a slow
    publication of the newest cycle falls back to the previous one instead of
    failing the whole request; probe failure keeps the time-based heuristic.
    """
    if anchor_utc.tzinfo is None:
        raise ValueError("nowcast anchor must be timezone-aware")
    anchor = anchor_utc.astimezone(timezone.utc)
    reference = (
        resolve_reference_time(request, anchor)
        if reference_utc is None
        else reference_utc
    )
    if reference.tzinfo is None:
        raise ValueError("nowcast reference must be timezone-aware")
    reference = reference.astimezone(timezone.utc)
    if request.gfs_cycle_override is not None:
        return choose_model_cycle("gfs", reference, request.gfs_cycle_override)
    cycle_reference = anchor if reference > anchor else reference
    if reference <= anchor:
        # Live request (event not yet passed): trust only a cycle NOMADS
        # actually serves instead of the fixed publication-lag guess.  The
        # scheduler owns probe provenance, so this request-scoped probe does
        # not overwrite it.
        return query_latest_gfs_cycle(cycle_reference, record_provenance=False)
    return choose_model_cycle("gfs", cycle_reference, None)


class ForecastService:
    def __init__(
        self,
        gfs_client: GfsNomadsClient,
        aerosol_provider: AerosolProvider,
        nowcast_analyzer: Optional[JmaNowcastAnalyzer] = None,
        particulate_provider: Optional[Callable[..., Any]] = None,
        radar_provider: Optional[Callable[..., Any]] = None,
        progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self.aerosol_provider = aerosol_provider
        self.nowcast_vertical_prior_provider = GfsNowcastVerticalPriorProvider(gfs_client)
        self.nowcast_analyzer = nowcast_analyzer or JmaNowcastAnalyzer(
            vertical_prior_provider=self.nowcast_vertical_prior_provider,
        )
        if nowcast_analyzer is not None and getattr(self.nowcast_analyzer, "vertical_prior_provider", None) is None:
            self.nowcast_analyzer.vertical_prior_provider = self.nowcast_vertical_prior_provider
        self.particulate_provider = particulate_provider or fetch_particulate_reading
        self.radar_provider = radar_provider or fetch_weather_radar
        self.progress_callback = progress_callback

    def forecast(self, request: ForecastRequest) -> ForecastReport:
        if request.forecast_mode != 'nowcast' or request.model_source != 'satellite':
            raise ValueError(
                'ForecastService only handles satellite nowcast; model forecasts use the unified national native core.'
            )
        requested = normalize_compute_backend(request.compute_backend)
        if requested == 'gpu' and not native_gpu_available():
            raise RuntimeError('GPU 计算后端不可用；请使用 CPU，或构建带 gpu feature 的 Rust 内核。')
        with compute_backend_scope(requested) as backend_trace:
            report = self._forecast(request)
        return replace(
            report,
            compute_backend_requested=requested,
            compute_backend=backend_trace.actual,
        )

    def _forecast(self, request: ForecastRequest) -> ForecastReport:
        self._progress('prepare', 5, '计算太阳事件和评估时间窗')
        anchor_local = solar_event_time_local(request.target_date, request.point, request.timezone_name, request.phenomenon)
        anchor_utc = anchor_local.astimezone(timezone.utc)
        reference_utc = resolve_reference_time(request, anchor_utc)
        anchor_solar = solar_snapshot(request.point, anchor_utc, request.timezone_name)
        return self._forecast_nowcast(
            request,
            anchor_local,
            anchor_utc,
            reference_utc,
            anchor_solar.azimuth_deg,
        )

    def _forecast_nowcast(
        self,
        request: ForecastRequest,
        anchor_local: datetime,
        anchor_utc: datetime,
        reference_utc: datetime,
        glow_azimuth_deg: float,
    ) -> ForecastReport:
        event_offset_minutes = (reference_utc - anchor_utc).total_seconds() / 60.0
        retrospective = reference_utc > anchor_utc
        source_reference_utc = nowcast_source_reference_time(reference_utc, anchor_utc)
        satellite_reference_utc = satellite_lookup_reference_time(reference_utc, anchor_utc)
        if not retrospective and not nowcast_event_uses_satellite_scoring(event_offset_minutes):
            raise ValueError(
                '实况仅支持事件（日出/日落）前后 4 小时内；当前目标超出该窗口，'
                '请改用模式预测（GFS/EC）或调整目标日期。'
            )
        # Freeze the cycle before any concurrent provider work. Every sampled
        # valid time in this request must use the same available GFS cycle.
        request = replace(
            request,
            reference_time_utc=(
                request.reference_time_utc
                if request.reference_time_utc is not None
                else reference_utc
            ),
        )
        request = replace(
            request,
            gfs_cycle_override=resolve_nowcast_gfs_cycle(
                request,
                anchor_utc,
                reference_utc=reference_utc,
            ),
        )
        if retrospective:
            self._progress(
                'satellite',
                10,
                f'历史回顾：读取 {anchor_utc:%Y-%m-%d %H:%MZ} 事件窗口内的 Himawari HSD 扫描',
            )
        self._progress('environment', 12, '并行读取 Himawari 多光谱、天气雷达、AOD 和 PM2.5 环境')
        executor = ThreadPoolExecutor(max_workers=4)
        environment_deadline = time.monotonic() + ENVIRONMENT_INPUTS_TIMEOUT_SECONDS
        try:
            aerosol_future = executor.submit(self.aerosol_provider.get_reading, request)
            particulate_future = executor.submit(self.particulate_provider, request, anchor_utc)
            nowcast_future = executor.submit(
                self.nowcast_analyzer.analyze,
                request,
                satellite_reference_utc,
                glow_azimuth_deg,
                anchor_utc,
            )
            radar_future = executor.submit(
                self.radar_provider,
                request,
                source_reference_utc,
                glow_azimuth_deg,
            )
            aerosol = _future_result_with_deadline(
                aerosol_future, environment_deadline
            )
            try:
                pm2_5, pm10, particulate_source = _future_result_with_deadline(
                    particulate_future, environment_deadline
                )
            except Exception:
                pm2_5, pm10, particulate_source = None, None, None
            nowcast = _future_result_with_deadline(nowcast_future, environment_deadline)
            try:
                radar = _future_result_with_deadline(radar_future, environment_deadline)
            except Exception:
                radar = None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if aerosol is None:
            raise RuntimeError('没有可用的 AOD 数据源返回结果。')
        aerosol = replace(
            aerosol,
            pm2_5_ug_m3=pm2_5,
            pm10_ug_m3=pm10,
            particulate_source=particulate_source,
        )
        if nowcast is None:
            raise RuntimeError(
                '实况分析不可用[卫星观测缺失]：Himawari 校准扫描（HSD）在新鲜度窗口内'
                '没有完整帧。请检查 /api/v1/firesky/satellite/cache/warmup 的预热结果、'
                '.cache/himawari-hsd/ 下最近扫描是否完整，以及 NOAA S3 连通性。'
            )
        nowcast_failures: list[str] = []
        if nowcast.cloud_column_schema_version != NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION:
            nowcast_failures.append(
                f'云柱 schema 版本不匹配（得到 {nowcast.cloud_column_schema_version}，'
                f'需要 {NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION}）'
            )
        if nowcast.satellite_retrieval_quality == 'unknown':
            nowcast_failures.append(
                '卫星反演质量为 unknown：采样点覆盖率或校准质量不足'
                '（需要 ≥80% 采样点可用且非 unknown）'
            )
        if not nowcast.cloud_column_sets:
            nowcast_failures.append('没有生成任何云柱集合：卫星帧或 GFS 垂直先验缺失')
        if not nowcast.observed_features_by_offset:
            nowcast_failures.append('事件窗口内没有落在 5 分钟时间轴上的观测帧')
        observed = set(nowcast.observed_features_by_offset)
        observed_times = set(nowcast.observed_feature_times_by_offset)
        if not observed.issubset(observed_times):
            nowcast_failures.append(
                f'观测时间轴不一致：{sorted(observed - observed_times)} 缺少对应观测时间'
            )
        if nowcast_failures:
            raise RuntimeError(
                '实况分析不可用[' + '；'.join(nowcast_failures) + ']：'
                '需要覆盖足够采样点的校准 Himawari 云柱和同周期 GFS vertical prior；'
                '缺失或 unknown 不能发布为数值晴空结果。'
            )
        if radar is not None:
            nowcast = replace(
                nowcast,
                radar_source=radar.source,
                radar_valid_time_utc=radar.valid_time_utc,
                radar_age_minutes=radar.age_minutes,
                radar_coverage_ratio=radar.coverage_ratio,
                radar_local_precip_ratio=radar.local_precip_ratio,
                radar_path_precip_ratio=radar.path_precip_ratio,
                radar_convective_ratio=radar.convective_ratio,
                radar_max_dbz=radar.max_dbz,
                radar_convective_risk=radar.convective_risk,
            )
            max_dbz_text = f'{radar.max_dbz:.0f} dBZ' if radar.max_dbz is not None else '无有效回波'
            nowcast.summary.append(
                f'天气雷达 {radar.valid_time_utc:%H:%MZ}：最大反射率 {max_dbz_text}，'
                f'本地降水回波 {radar.local_precip_ratio * 100:.0f}%、'
                f'光路降水回波 {radar.path_precip_ratio * 100:.0f}%、'
                f'对流厚云风险 {radar.convective_risk * 100:.0f}/100。'
            )
        else:
            nowcast.summary.append('天气雷达实况不可用或杭州区域无有效覆盖；不以无回波推断无云。')
        self._progress('satellite', 65, '分析多光谱云相、云幕纹理、雷达对流和光路通透度')
        event_direction = '日出' if request.phenomenon == 'dawn' else '日落'
        if aerosol.quality == 'fallback':
            # The nowcast path has no quality gate on the aerosol value (the
            # EC/native paths reject fallback AOD outright), so at minimum the
            # summary must say the number is a constant fallback, not CAMS.
            nowcast.summary.append(
                f'注意：当前 AOD {aerosol.aod550:.2f} 来自回退常数（{aerosol.source}），'
                '不是 CAMS 实时值；实况评分的气溶胶修正按该常数计，可信度受限。'
            )
        nowcast.summary.append(
            f'{event_direction}方向云幕约 {nowcast.horizon_cloud_ratio * 100:.0f}%、'
            f'通透度约 {nowcast.horizon_clearance_score * 100:.0f}%；'
            '该指标与校准 HSD 云柱同源，仅作 horizon 诊断，不重复施加评分上限。'
        )
        # Score only independent calibrated scans actually present in the event
        # window. A projected future cloud field remains one target-time sample.
        base_assessments, diagnosed_nowcast = nowcast_observation_assessments(
            request, nowcast, aerosol
        )
        assessments = base_assessments
        core_indexes = nowcast_core_indexes(assessments, anchor_utc)
        assessments, quality, best_index = apply_unified_event_quality(
            assessments,
            aerosol.aod550,
            event_time_local=anchor_local,
            core_window_minutes=NOWCAST_CORE_WINDOW_MINUTES,
            apply_duration=nowcast_duration_is_qualified(nowcast, assessments),
        )
        burnable_core_indices = [
            index
            for index in core_indexes
            if index < len(quality.get("steps", []))
            and quality["steps"][index]["physically_burnable"]
        ]
        best_index = max(
            burnable_core_indices,
            key=lambda index: float(quality["steps"][index]["instant_quality_index"]),
            default=None,
        )
        diagnostic_index = best_index
        if diagnostic_index is None and core_indexes:
            diagnostic_index = max(
                core_indexes,
                key=lambda index: float(
                    quality["steps"][index]["instant_quality_index"]
                ),
            )
        diagnostic = assessments[diagnostic_index] if diagnostic_index is not None else None
        nowcast = diagnosed_nowcast
        geometric_blocking = nowcast_geometric_path_blocking_risk(diagnostic, nowcast)
        geometric_samples = (
            diagnostic.cross_section.samples_count
            if diagnostic is not None and diagnostic.cross_section is not None
            else 0
        )
        nowcast = replace(
            nowcast,
            geometric_path_blocking_risk=geometric_blocking,
            geometric_path_samples=geometric_samples,
            solar_path_profile=(
                list(diagnostic.cross_section.profile)
                if diagnostic is not None and diagnostic.cross_section is not None
                else []
            ),
            summary=[
                *nowcast.summary,
                (
                    f'太阳入射光路几何剖面：{geometric_samples} 个沿线样本，'
                    f'云柱相交挡光风险 {geometric_blocking * 100:.0f}/100。'
                ),
                (
                    f'太阳方向实况大气剖面已输出：{geometric_samples} 个距离点，'
                    '含云底/云顶、压力层云量代理和太阳入射阈值。'
                ),
            ],
        )
        best = assessments[best_index] if best_index is not None else None
        event_duration = (
            int(round(float(quality["quality_components"].get("duration_minutes", 0.0))))
            if quality.get("duration_status") == "estimated"
            else None
        )
        # ForecastReport's traditional headline fields are probabilities in
        # the 0..1 range. Keep the authoritative unified event quality in the
        # API's separate quality_index field (0..2.5), but normalize it before
        # placing it in the report so CLI, HTML and history cannot emit >100.
        event_score = clamp(
            float(quality["quality_index"]) / FIRE_CLOUD_QUALITY_MAX_SCORE
        )
        best_quality_score = (
            clamp(
                float(quality["steps"][best_index]["instant_quality_index"])
                / FIRE_CLOUD_QUALITY_MAX_SCORE
            )
            if best_index is not None
            else 0.0
        )
        step_characteristics = []
        for index in core_indexes:
            step = assessments[index]
            quality_step = quality["steps"][index]
            step_characteristics.append({
                "valid_time_local": step.valid_time_local.isoformat(),
                "burn_score": clamp(
                    float(quality_step["instant_quality_index"])
                    / FIRE_CLOUD_QUALITY_MAX_SCORE
                ),
                "instant_quality_index": quality_step["instant_quality_index"],
                "physically_burnable": quality_step["physically_burnable"],
                "burnable_cloud_coverage": quality_step["burnable_cloud_coverage"],
                "scene_scores": step.scene_scores,
                "local_sky_score": step.local_sky_score,
                "remote_sky_score": step.remote_sky_score,
            })
        summary = [
            *([(
                f'历史回顾：目标事件已结束，按事件窗口内最近的 Himawari HSD 扫描重建；'
                '当前时刻云图不参与本次事件评分。'
            )] if retrospective else []),
            f'本次使用 {len(assessments)} 个事件窗口内的独立 HSD 扫描时次；校准 HSD 云柱与同周期 GFS 温湿度/位势高度 vertical prior 共同评分，缓存缺失时自动下载所需分段，不把 JPEG/NICT 灰度当作云柱。',
            f'主报告按事件核心 ±{NOWCAST_CORE_WINDOW_MINUTES} 分钟选峰和统计；外围时刻仅用于观察云场趋势。',
            (
                f'共评估 {len(assessments)} 个实况时刻，最佳时刻为 {best.valid_time_local:%H:%M}，'
                + (
                    f'物理可烧云持续约 {event_duration} 分钟。'
                    if event_duration is not None
                    else '独立 HSD 扫描不足以估算持续时间；质量发布为瞬时值，持续时间 unknown。'
                )
                if best is not None
                else f'共评估 {len(assessments)} 个实况时刻，事件窗内没有通过物理可烧门槛的时次。'
            ),
            *nowcast.summary,
        ]
        self._progress('summary', 85, '卫星实况评分和摘要已完成')
        return ForecastReport(
            observer_name=request.name,
            phenomenon=request.phenomenon,
            model_source='satellite',
            forecast_mode='nowcast',
            target_date=request.target_date,
            timezone_name=request.timezone_name,
            anchor_local=anchor_local,
            cycle_time_utc=nowcast.image_valid_time_utc,
            model_cycles={},
            aerosol=aerosol,
            overall_score=event_score,
            burn_score=best_quality_score,
            intensity_score=best.intensity_score if best is not None else 0.0,
            extent_score=best.extent_score if best is not None else 0.0,
            vividness_score=best.vividness_score if best is not None else 0.0,
            duration_minutes=event_duration,
            potential_duration_minutes=event_duration,
            confidence_score=nowcast.confidence,
            model_disagreement_score=0.0,
            best_time_local=best.valid_time_local if best is not None else None,
            summary=summary,
            steps=assessments,
            nowcast=nowcast,
            model_weights={},
            model_weight_samples={},
            model_component_scores={},
            selected_regime=best.selected_regime if best is not None else "unknown",
            scene_scores=dict(best.scene_scores) if best is not None else {},
            local_sky_score=best.local_sky_score if best is not None else 0.0,
            remote_sky_score=best.remote_sky_score if best is not None else 0.0,
            convective_risk=best.convective_risk if best is not None else 0.0,
            color_diagnosis=dict(best.color_diagnosis) if best is not None else {},
            cloud_evolution=(
                cloud_evolution_diagnosis(
                    step_characteristics,
                    best_index=best_index,
                )
                if best_index is not None
                else cloud_evolution_diagnosis([])
            ),
            timing_window=(
                score_curve_window(step_characteristics, regime=best.selected_regime)
                if best is not None
                else None
            ),
        )

    def _progress(self, stage: str, percent: int, message: str, **details: Any) -> None:
        if self.progress_callback is None:
            return
        event: dict[str, Any] = {
            'stage': stage,
            'percent': max(0, min(99, int(percent))),
            'message': message,
        }
        if details:
            event['details'] = details
        self.progress_callback(event)

def nowcast_core_indexes(
    assessments: Sequence[TimeStepAssessment],
    anchor_utc: datetime,
) -> List[int]:
    """Return the event-centered nowcast steps used for the headline verdict."""
    core = [
        index
        for index, step in enumerate(assessments)
        if abs((step.valid_time_utc - anchor_utc).total_seconds())
        <= NOWCAST_CORE_WINDOW_MINUTES * 60.0
    ]
    return core or list(range(len(assessments)))


def nowcast_duration_is_qualified(
    nowcast: NowcastAdjustment,
    assessments: Sequence[TimeStepAssessment],
) -> bool:
    """Allow duration integration only from source times actually scored."""
    if len(assessments) < 2:
        return False
    source_times = {
        source_time
        for step in assessments
        for source_time in step.cloud_source_times_utc
    }
    if len(source_times) >= 2:
        return True
    return (
        len({step.valid_time_utc for step in assessments}) >= 2
        and nowcast.satellite_motion_quality in {"strong", "decayed"}
        and nowcast.satellite_advection_horizon_hours > 0.0
    )


def apply_unified_event_quality(
    assessments: Sequence[TimeStepAssessment],
    aod550: float,
    *,
    event_time_local: Optional[datetime] = None,
    core_window_minutes: Optional[float] = None,
    apply_duration: bool = True,
) -> tuple[List[TimeStepAssessment], dict[str, Any], Optional[int]]:
    """Apply the canonical observer-field event score to satellite steps."""
    quality = fire_cloud_quality_from_assessments(
        assessments,
        aod550,
        event_time_local=event_time_local,
        core_window_minutes=core_window_minutes,
        apply_duration=apply_duration,
    )
    quality_steps = quality.get("steps", [])
    normalized: list[TimeStepAssessment] = []
    for index, step in enumerate(assessments):
        if index >= len(quality_steps):
            normalized.append(replace(step, burn_score=clamp(step.burn_score)))
            continue
        quality_step = quality_steps[index]
        normalized.append(
            replace(
                step,
                burn_score=clamp(
                    float(quality_step["instant_quality_index"])
                    / FIRE_CLOUD_QUALITY_MAX_SCORE
                ),
                # Keep every nowcast component on the same canonical observer
                # field as the headline score.
                intensity_score=clamp(
                    float(quality_step.get("forward_burnable_cloud_load", 0.0))
                    / FIRE_CLOUD_QUALITY_MAX_SCORE
                ),
                extent_score=clamp(
                    float(quality_step["forward_view_cloud_field_amount"])
                ),
                vividness_score=clamp(
                    float(quality_step.get("brightness_color", step.vividness_score))
                ),
                geometric_cloud_coverage=clamp(
                    float(quality_step["forward_view_cloud_field_amount"])
                ),
            )
        )
    best_index = quality.get("best_index")
    if best_index is not None:
        best_index = int(best_index)
    return normalized, quality, best_index


def fetch_weather_radar(
    request: ForecastRequest,
    reference_utc: datetime,
    path_azimuth_deg: float,
):
    from firesky.radar import fetch_radar_observation

    return fetch_radar_observation(request, reference_utc, path_azimuth_deg)


def assess_satellite_snapshot(
    request: ForecastRequest,
    snapshot: DomainSnapshot,
    aerosol: AerosolReading,
) -> TimeStepAssessment:
    if snapshot.model_name != 'satellite':
        raise ValueError('satellite nowcast assessment requires a satellite snapshot')
    solar = solar_snapshot(request.point, snapshot.valid_time_utc, request.timezone_name)
    base = assess_satellite_timestep(
        snapshot,
        solar.elevation_deg,
        solar.azimuth_deg,
        aerosol,
        include_map_cells=request.include_map_cells,
        map_radius_km=request.radius_km,
        observer_elevation_m=request.point.elevation_m,
    )
    return TimeStepAssessment(
        forecast_hour=base.forecast_hour,
        valid_time_utc=snapshot.valid_time_utc,
        valid_time_local=solar.valid_time_local,
        solar_elevation_deg=solar.elevation_deg,
        solar_azimuth_deg=solar.azimuth_deg,
        burn_score=base.burn_score,
        intensity_score=base.intensity_score,
        extent_score=base.extent_score,
        vividness_score=base.vividness_score,
        visibility_score=base.visibility_score,
        low_cloud_penalty=base.low_cloud_penalty,
        candidate_count=base.candidate_count,
        burning_cloud_count=base.burning_cloud_count,
        top_candidate=base.top_candidate,
        strong_burning_cloud_count=base.strong_burning_cloud_count,
        top_boundary=base.top_boundary,
        boundary_score=base.boundary_score,
        boundary_uncertainty=base.boundary_uncertainty,
        top_cloud_hole=base.top_cloud_hole,
        cloud_hole_score=base.cloud_hole_score,
        cross_section=base.cross_section,
        solar_inflow_blockage=base.solar_inflow_blockage,
        model_disagreement_score=base.model_disagreement_score,
        selected_regime=base.selected_regime,
        scene_scores=dict(base.scene_scores),
        local_sky_score=base.local_sky_score,
        remote_sky_score=base.remote_sky_score,
        convective_risk=base.convective_risk,
        color_diagnosis=dict(base.color_diagnosis),
        cloud_source_times_utc=[snapshot.valid_time_utc],
        reasons=[
            '云场来源：卫星反演标准云格点。',
            (
                f'云场来源时次：{snapshot.valid_time_utc:%Y-%m-%d %H:%MZ}；'
                f'霞光评估时刻：{snapshot.valid_time_utc:%Y-%m-%d %H:%MZ}。'
            ),
        ] + base.reasons,
        map_cells=base.map_cells,
        geometric_cloud_coverage=base.geometric_cloud_coverage,
        structure_gate_score=base.structure_gate_score,
        forward_view_cloud_field_amount=base.forward_view_cloud_field_amount,
        forward_burnable_cloud_field_amount=base.forward_burnable_cloud_field_amount,
        observer_local_cloud_field_amount=base.observer_local_cloud_field_amount,
    )


def evaluation_times_utc(request: ForecastRequest, anchor_local: datetime) -> List[datetime]:
    full_window = range(-75, 21) if request.phenomenon == 'dawn' else range(-20, 76)
    dense_start, dense_end = (-45, 20) if request.phenomenon == 'dawn' else (-20, 45)
    offsets_minutes = [
        offset
        for offset in full_window
        if dense_start <= offset <= dense_end or offset % 5 == 0
    ]
    phase_candidates = [anchor_local + timedelta(minutes=offset) for offset in offsets_minutes]

    valid: List[datetime] = []
    for local_time in phase_candidates:
        when_utc = local_time.astimezone(timezone.utc)
        elevation = solar_snapshot(request.point, when_utc, request.timezone_name).elevation_deg
        if -7.5 <= elevation <= 1.5:
            valid.append(when_utc)
    anchor_utc = anchor_local.astimezone(timezone.utc)
    if anchor_utc not in valid:
        valid.append(anchor_utc)
    valid.sort()
    return valid


def required_model_hours(
    model_name: str,
    cycle_utc: datetime,
    evaluation_times: List[datetime],
) -> List[int]:
    if model_name == 'ec':
        from firesky.ecmwf_point import required_ecmwf_forecast_hours

        return list(required_ecmwf_forecast_hours(cycle_utc, evaluation_times))
    if model_name != 'gfs':
        raise ValueError('霞光预测模式来源必须是 GFS 或 EC。')
    hours: set[int] = set()
    for evaluation_time in evaluation_times:
        offset = max(0.0, (evaluation_time - cycle_utc).total_seconds() / 3600.0)
        cadence = 3 if offset > 120.0 else 1
        lower = max(0, int(math.floor(offset / cadence) * cadence))
        upper = max(0, int(math.ceil(offset / cadence) * cadence))
        hours.update((lower, upper))
    return sorted(hours)

def satellite_nowcast_assessment(
    request: ForecastRequest,
    anchor_utc: datetime,
    nowcast: NowcastAdjustment,
    aerosol: AerosolReading,
    *,
    offset_minutes: int,
) -> TimeStepAssessment:
    # Calibrated cloud columns are the only satellite scoring source.  An
    # unknown/missing column set stays empty; it is not reconstructed from
    # display-image ratios or fixed pressure layers.
    if offset_minutes not in nowcast.observed_features_by_offset:
        raise ValueError("nowcast assessment offset has no calibrated observation")
    if offset_minutes not in nowcast.observed_feature_times_by_offset:
        raise ValueError("nowcast assessment offset has no calibrated source time")
    source_time = nowcast.observed_feature_times_by_offset[offset_minutes]
    source_time = source_time.astimezone(timezone.utc)
    column_sets = _cloud_column_sets_for_observation_time(nowcast, source_time)
    features = list(nowcast.observed_features_by_offset[offset_minutes])
    snapshot = DomainSnapshot(
        model_name='satellite',
        forecast_hour=0,
        # Each assessment is a direct observation at its own scan time.  Do
        # not use the primary/projected image time as the vertical prior time
        # for a different scan.
        init_time_utc=source_time,
        valid_time_utc=source_time,
        features=features,
        source_url=nowcast.source,
        cache_path='',
    )
    assessment = assess_satellite_snapshot(request, snapshot, aerosol)
    return replace(
        assessment,
        satellite_retrieval_material_evidence=(
            _satellite_retrieval_material_evidence_for_sets(column_sets)
        ),
    )


def nowcast_observation_assessments(
    request: ForecastRequest,
    nowcast: NowcastAdjustment,
    aerosol: AerosolReading,
    *,
    backend: Optional[str] = None,
) -> tuple[List[TimeStepAssessment], NowcastAdjustment]:
    """Score every calibrated scan and apply the radar constraints.

    `backend="python"` scores each scan through satellite_nowcast_assessment
    and applies apply_nowcast_observation_constraints per step.  `backend=
    "rust"` issues one native batch call whose output already carries the
    radar-constrained assessments (same field semantics); the nowcast-side
    diagnosis still happens in Python for the first scan.  Returns the
    constrained assessments and the diagnosed nowcast.
    """
    from firesky.rust_desktop_data import nowcast_assessment_backend

    backend = backend or nowcast_assessment_backend()
    offsets = sorted(nowcast.observed_features_by_offset)
    if backend == "rust":
        from firesky.rust_desktop_data import (
            nowcast_assessment,
            nowcast_assessment_as_model,
        )

        radar = {
            "source": nowcast.radar_source,
            "coverage_ratio": nowcast.radar_coverage_ratio,
            "age_minutes": nowcast.radar_age_minutes,
            "max_dbz": nowcast.radar_max_dbz,
            "local_precip_ratio": nowcast.radar_local_precip_ratio,
            "path_precip_ratio": nowcast.radar_path_precip_ratio,
            "convective_ratio": nowcast.radar_convective_ratio,
            "convective_risk": nowcast.radar_convective_risk,
        }
        scans = []
        for offset in offsets:
            source_time = nowcast.observed_feature_times_by_offset[offset].astimezone(
                timezone.utc
            )
            column_sets = _cloud_column_sets_for_observation_time(nowcast, source_time)
            solar = solar_snapshot(request.point, source_time, request.timezone_name)
            scans.append({
                "features": list(nowcast.observed_features_by_offset[offset]),
                "valid_time_utc": source_time,
                "forecast_hour": 0,
                "solar_elevation_deg": solar.elevation_deg,
                "solar_azimuth_deg": solar.azimuth_deg,
                "radar": radar,
                "satellite_material_evidence": (
                    _satellite_retrieval_material_evidence_for_sets(column_sets)
                ),
            })
        payload = nowcast_assessment(
            scans=scans,
            timezone_name=request.timezone_name,
            aerosol=aerosol,
            observer_elevation_m=request.point.elevation_m,
            map_radius_km=request.radius_km,
            include_map_cells=request.include_map_cells,
        )
        rebuilt = nowcast_assessment_as_model(payload, scans=scans)["assessments"]
        # assess_satellite_snapshot wraps each step: cloud_source_times_utc and
        # the two provenance reason lines come from the satellite wrapper.
        assessments = []
        for source_time, step in zip(
            (nowcast.observed_feature_times_by_offset[offset].astimezone(timezone.utc) for offset in offsets),
            rebuilt,
            strict=True,
        ):
            assessments.append(
                replace(
                    step,
                        cloud_source_times_utc=[source_time],
                    # assess_satellite_snapshot does not carry the point-scorer
                    # selected_* diagnostics; the batch contract mirrors that.
                    selected_observer_path_aod550=None,
                    selected_inflow_path_aod550=None,
                    selected_aerosol_path_variability_score=0.0,
                    reasons=[
                        '云场来源：卫星反演标准云格点。',
                        (
                            f'云场来源时次：{source_time:%Y-%m-%d %H:%MZ}；'
                            f'霞光评估时刻：{source_time:%Y-%m-%d %H:%MZ}。'
                        ),
                    ] + step.reasons,
                )
            )
        return assessments, _diagnose_nowcast_radar(nowcast)
    if backend != "python":
        raise ValueError("nowcast assessment backend must be python or rust")
    base_assessments = [
        satellite_nowcast_assessment(
            request,
            nowcast.observed_feature_times_by_offset[observed_offset],
            nowcast,
            aerosol,
            offset_minutes=observed_offset,
        )
        for observed_offset in offsets
    ]
    constrained_results = []
    diagnosed_nowcast = nowcast
    for index, step in enumerate(base_assessments):
        constrained, diagnosed = apply_nowcast_observation_constraints(
            step,
            nowcast,
            step.valid_time_utc,
            path_azimuth_deg=step.solar_azimuth_deg,
            observer_elevation_m=request.point.elevation_m,
            diagnose_nowcast=index == 0,
        )
        if index == 0:
            diagnosed_nowcast = diagnosed
        constrained_results.append(constrained)
    return constrained_results, diagnosed_nowcast


def _cloud_column_sets_for_observation_time(
    nowcast: NowcastAdjustment,
    source_time: datetime,
) -> list[dict[str, Any]]:
    """Return only cloud columns from the scan used by one nowcast step."""
    source_time = source_time.astimezone(timezone.utc)
    matching: list[dict[str, Any]] = []
    for cloud_set in nowcast.cloud_column_sets:
        try:
            cloud_time = datetime.fromisoformat(str(cloud_set["valid_time_utc"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("nowcast cloud-column set time is invalid") from exc
        if cloud_time.tzinfo is None or cloud_time.utcoffset() != timedelta(0):
            raise ValueError("nowcast cloud-column set time must be UTC")
        if cloud_time.astimezone(timezone.utc) == source_time:
            matching.append(cloud_set)
    if not matching:
        raise ValueError(
            "nowcast observation has no cloud-column set for its source time"
        )
    return matching


def _satellite_retrieval_material_evidence_for_sets(
    cloud_column_sets: Sequence[dict[str, Any]],
) -> Optional[float]:
    """Compute neutral HSD material evidence for one scan only."""
    usable = 0
    for cloud_set in cloud_column_sets:
        for column in cloud_set.get("columns", ()):
            probability = column.get("horizontal_cloud_probability")
            quality = str(column.get("retrieval_quality", "unknown"))
            if (
                quality != "unknown"
                and probability is not None
                and math.isfinite(float(probability))
                and float(probability) >= 0.08
            ):
                usable += 1
    return MATERIAL_REFERENCE_QUALITY if usable else None


def satellite_retrieval_material_evidence(
    nowcast: NowcastAdjustment,
) -> Optional[float]:
    """Return neutral material evidence for a usable calibrated cloud field.

    HSD B07/B13/B15 establishes calibrated cloud presence and height support,
    but it does not publish cloud condensate or optical depth. Returning zero
    would turn an unknown material measurement into an empty cloud field. The
    neutral reference is used only when a usable cloud column is present; it
    is not a CLWMR/ICMR substitute.
    """
    if (
        nowcast.cloud_column_schema_version != NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION
        or nowcast.satellite_retrieval_quality == "unknown"
        or not nowcast.cloud_column_sets
    ):
        return None
    return _satellite_retrieval_material_evidence_for_sets(nowcast.cloud_column_sets)


def observation_ramp(value: float, low: float, high: float) -> float:
    if high <= low:
        return 1.0 if value >= high else 0.0
    ratio = clamp((value - low) / (high - low))
    return ratio * ratio * (3.0 - 2.0 * ratio)


def radar_observation_reliability(nowcast: NowcastAdjustment) -> float:
    if (
        nowcast.radar_source is None
        or nowcast.radar_coverage_ratio is None
        or nowcast.radar_age_minutes is None
    ):
        return 0.0
    coverage = clamp(nowcast.radar_coverage_ratio)
    age_minutes = max(0.0, nowcast.radar_age_minutes)
    freshness = 1.0 if age_minutes <= 10.0 else clamp((70.0 - age_minutes) / 60.0)
    return clamp(coverage * freshness)


def radar_effective_convective_risk(nowcast: NowcastAdjustment) -> tuple[float, float]:
    reliability = radar_observation_reliability(nowcast)
    if reliability <= 0.0:
        return 0.0, 0.0
    dbz = nowcast.radar_max_dbz
    dbz_risk = observation_ramp(dbz, 28.0, 52.0) if dbz is not None else 0.0
    local_precip = clamp(nowcast.radar_local_precip_ratio or 0.0)
    path_precip = clamp(nowcast.radar_path_precip_ratio or 0.0)
    convective_coverage = clamp(nowcast.radar_convective_ratio or 0.0)
    reported_risk = clamp(nowcast.radar_convective_risk)
    precip_risk = max(local_precip, path_precip, convective_coverage)
    raw_risk = max(
        reported_risk,
        convective_coverage,
        0.72 * dbz_risk + 0.28 * precip_risk,
    )
    return reliability, clamp(reliability * raw_risk)


def radar_effective_path_blocking_risk(nowcast: NowcastAdjustment) -> float:
    """Estimate whether the radar-observed solar corridor is actually blocked."""
    reliability = radar_observation_reliability(nowcast)
    if reliability <= 0.0:
        return 0.0
    # The regional peak/local echo can sit outside the sunward corridor. Use
    # the directional path sample for a burnability veto and score ceiling.
    return clamp(reliability * clamp(nowcast.radar_path_precip_ratio or 0.0))


def nowcast_geometric_path_blocking_risk(
    assessment: Optional[TimeStepAssessment],
    nowcast: NowcastAdjustment,
) -> float:
    """Combine image-space horizon opacity with the ray/cloud-column geometry."""
    direct_blocking = clamp(1.0 - nowcast.horizon_clearance_score)
    cross_section = assessment.cross_section if assessment is not None else None
    if cross_section is None or cross_section.samples_count < 4:
        return direct_blocking
    geometric_blocking = clamp(cross_section.blocking_risk)
    return clamp(0.65 * direct_blocking + 0.35 * geometric_blocking)


def _nowcast_radar_reason(
    radar_reliability: float,
    convective_risk: float,
    path_blocking_risk: float,
    convective_ceiling: float,
) -> str:
    reason = (
        f'雷达约束：可靠度 {radar_reliability * 100:.0f}/100，'
        f'区域对流风险 {convective_risk * 100:.0f}/100，'
        f'日落光路阻塞风险 {path_blocking_risk * 100:.0f}/100，'
        f'最终分数上限 {convective_ceiling * 100:.0f}/100。'
    )
    if radar_reliability < 0.25:
        reason += '覆盖或时效不足，不施加强约束，也不用无回波推断晴空。'
    return reason


def _diagnose_nowcast_radar(nowcast: NowcastAdjustment) -> NowcastAdjustment:
    """Nowcast-side radar diagnosis (the `diagnose_nowcast` half of
    apply_nowcast_observation_constraints), used when the assessment-side
    radar constraints were already applied inside the native batch."""
    radar_reliability, convective_risk = radar_effective_convective_risk(nowcast)
    path_blocking_risk = radar_effective_path_blocking_risk(nowcast)
    convective_ceiling = clamp(
        1.0 - 0.88 * observation_ramp(path_blocking_risk, 0.18, 0.72),
        low=0.12,
    )
    diagnostics = list(nowcast.summary)
    if nowcast.radar_source is not None:
        diagnostics.append(
            _nowcast_radar_reason(
                radar_reliability, convective_risk, path_blocking_risk, convective_ceiling
            )
        )
    return replace(
        nowcast,
        radar_reliability_score=radar_reliability,
        effective_convective_risk=convective_risk,
        effective_path_blocking_risk=path_blocking_risk,
        convective_score_ceiling=convective_ceiling,
        summary=diagnostics,
    )


def apply_nowcast_observation_constraints(
    assessment: TimeStepAssessment,
    nowcast: NowcastAdjustment,
    anchor_utc: datetime,
    path_azimuth_deg: Optional[float] = None,
    observer_elevation_m: Optional[float] = None,
    *,
    diagnose_nowcast: bool = True,
) -> tuple[TimeStepAssessment, NowcastAdjustment]:
    radar_reliability, convective_risk = radar_effective_convective_risk(nowcast)
    path_blocking_risk = radar_effective_path_blocking_risk(nowcast)
    convective_ceiling = clamp(
        1.0 - 0.88 * observation_ramp(path_blocking_risk, 0.18, 0.72),
        low=0.12,
    )

    burn_score = min(assessment.burn_score, convective_ceiling)
    intensity_score = min(assessment.intensity_score, convective_ceiling)
    extent_score = assessment.extent_score
    vividness_score = min(assessment.vividness_score, convective_ceiling)
    visibility_score = assessment.visibility_score
    scene_scores = dict(assessment.scene_scores)
    scene_scores["convective"] = max(
        float(scene_scores.get("convective", 0.0)),
        convective_risk,
    )
    positive_scene_score = max(
        (float(value) for key, value in scene_scores.items() if key != "convective"),
        default=0.0,
    )
    selected_regime = assessment.selected_regime
    if path_blocking_risk >= 0.62 and path_blocking_risk >= positive_scene_score:
        selected_regime = "convective"
    color_diagnosis = dict(assessment.color_diagnosis)
    if color_diagnosis:
        color_diagnosis["confidence"] = min(
            float(color_diagnosis.get("confidence", 0.0)),
            vividness_score,
            visibility_score,
        )
        if selected_regime == "convective":
            color_diagnosis["type"] = "muted_glow"
        color_inputs = dict(color_diagnosis.get("inputs") or {})
        color_inputs["deep_cloud_risk"] = max(
            float(color_inputs.get("deep_cloud_risk", 0.0)),
            convective_risk,
        )
        color_diagnosis["inputs"] = color_inputs

    reasons = list(assessment.reasons)
    diagnostics = list(nowcast.summary)
    if nowcast.radar_source is not None:
        radar_reason = _nowcast_radar_reason(
            radar_reliability, convective_risk, path_blocking_risk, convective_ceiling
        )
        reasons.append(radar_reason)
        diagnostics.append(radar_reason)
    score_ceiling = convective_ceiling
    if assessment.observation_score_ceiling is not None:
        score_ceiling = min(score_ceiling, assessment.observation_score_ceiling)
    constrained = replace(
        assessment,
        burn_score=clamp(burn_score),
        intensity_score=clamp(intensity_score),
        extent_score=extent_score,
        vividness_score=clamp(vividness_score),
        visibility_score=clamp(visibility_score),
        low_cloud_penalty=assessment.low_cloud_penalty,
        convective_risk=max(assessment.convective_risk, convective_risk),
        selected_regime=selected_regime,
        scene_scores=scene_scores,
        color_diagnosis=color_diagnosis,
        reasons=reasons,
        observation_burnable=(
            assessment.observation_burnable and path_blocking_risk < 0.62
        ),
        observation_score_ceiling=(score_ceiling if score_ceiling < 1.0 else None),
    )
    diagnosed = (
        replace(
            nowcast,
            radar_reliability_score=radar_reliability,
            effective_convective_risk=convective_risk,
            effective_path_blocking_risk=path_blocking_risk,
            convective_score_ceiling=convective_ceiling,
            summary=diagnostics,
        )
        if diagnose_nowcast
        else nowcast
    )
    return constrained, diagnosed


def resolve_reference_time(request: ForecastRequest, anchor_utc: datetime) -> datetime:
    reference = request.reference_time_utc or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    if request.forecast_mode == 'nowcast':
        return reference
    return min(reference, anchor_utc + timedelta(minutes=30))


def choose_model_cycle(model_name: str, reference_utc: datetime, override_cycle: Optional[datetime]) -> datetime:
    if override_cycle is not None:
        if override_cycle.tzinfo is None or override_cycle.utcoffset() != timedelta(0):
            raise ValueError("model cycle override must be an aware UTC datetime")
        return override_cycle
    if model_name == 'gfs':
        return choose_gfs_cycle(reference_utc)
    if model_name == 'ec':
        return choose_ecmwf_cycle(reference_utc)
    raise RuntimeError(f'不支持的模式源：{model_name}')


def normalize_forecast_hours(model_name: str, forecast_hours: List[int]) -> List[int]:
    if model_name == 'ec':
        return sorted({max(0, int(round(hour / 3.0) * 3)) for hour in forecast_hours})
    if model_name == 'gfs':
        return sorted({hour if hour <= 120 else max(0, int(round(hour / 3.0) * 3)) for hour in forecast_hours})
    return sorted(set(forecast_hours))
