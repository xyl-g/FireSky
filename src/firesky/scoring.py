from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Mapping
import math
from statistics import mean
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from firesky.forecast_characteristics import (
    cloud_phase_diagnosis,
    color_diagnosis,
    scene_diagnosis,
)
from firesky.geometry import (
    EARTH_RADIUS_KM,
    angular_difference_deg,
    cloud_edge_distance_quality,
    cloud_edge_geometry_score,
    cloud_edge_max_distance_km,
    cloud_edge_reach_km,
    haversine_km,
    initial_bearing_deg,
    required_illumination_height_m,
    solar_illumination_elevation_deg,
)
from firesky.models import (
    AerosolReading,
    BoundaryUncertainty,
    BoundarySignal,
    CandidateSignal,
    CloudColumn,
    CloudHoleSignal,
    DomainSnapshot,
    GridCellFeature,
    GeoPoint,
    MapCloudCell,
    SolarInflowBlockage,
    TimeStepAssessment,
)
from firesky.analysis_constants import MAX_ANALYSIS_RADIUS_KM
from firesky.quality_core import duration_quality_factor
from firesky.native_core import (
    FIELD_COUNT,
    FEATURE_ROW_V2_FIELD_COUNT,
    NativeFeatureBuffer,
    NativeFeatureList,
    NativeSnapshotFeatures,
    aggregate_cloud_columns as native_aggregate_cloud_columns,
    aerosol_transmission_batch as native_aerosol_transmission_batch,
    aerosol_transmission_samples as native_aerosol_transmission_samples,
    boundary_prefilter as native_boundary_prefilter,
    boundary_clearance_weights as native_boundary_clearance_weights,
    boundary_context_scores as native_boundary_context_scores,
    cloud_hole_lightpath_signals_native as native_cloud_hole_signals,
    candidate_prefilter as native_candidate_prefilter,
    current_compute_backend,
    feature_metrics as native_feature_metrics,
    native_available,
    path_clearance_weights as native_path_clearance_weights,
)


MIN_CANDIDATE_DISTANCE_KM = 35.0
MAX_CANDIDATE_DISTANCE_KM = MAX_ANALYSIS_RADIUS_KM
LOCAL_UPPER_CANDIDATE_MAX_DISTANCE_KM = 35.0
BURNABLE_CELL_SCORE = 0.25
STRONG_CELL_SCORE = 0.45
MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM = MAX_ANALYSIS_RADIUS_KM
MAX_SOLAR_INFLOW_AEROSOL_DISTANCE_KM = MAX_ANALYSIS_RADIUS_KM
AEROSOL_SCALE_HEIGHT_M = 1800.0
AEROSOL_SCALE_HEIGHTS_M = (500.0, 1800.0, 4000.0)
LEGACY_AEROSOL_EFFECTIVE_COEFFICIENT = 0.45
FEATURE_BEARING_BIN_DEG = 2.0
_native_aerosol_enabled = True
_native_cloud_hole_enabled = True
LEGACY_LATITUDE_LINE_SPEED_KM_PER_MIN = 22.0


class FeatureSpatialIndex:
    def __init__(
        self,
        features: Sequence[GridCellFeature],
        bearing_bin_deg: float = FEATURE_BEARING_BIN_DEG,
        diagnostic_radius_km: float = float("inf"),
        observer_elevation_m: float = 0.0,
    ) -> None:
        self.bearing_bin_deg = bearing_bin_deg
        self.observer_elevation_m = float(observer_elevation_m)
        self.bin_count = max(1, int(round(360.0 / bearing_bin_deg)))
        self.by_bearing: Dict[int, List[GridCellFeature]] = {}
        self.blockers_by_bearing: Dict[int, List[GridCellFeature]] = {}
        self.by_pressure_bearing: Dict[tuple[int, int], List[GridCellFeature]] = {}
        self._features = features
        inherited_buffer = getattr(features, "native_buffer", None)
        self._native_buffer: Optional[NativeFeatureBuffer] = (
            inherited_buffer
            if isinstance(inherited_buffer, NativeFeatureBuffer) and inherited_buffer.length == len(features)
            else None
        )
        # The legacy native feature row predates the separate horizontal
        # satellite probability and conditional vertical profile.  A native
        # snapshot declares its own contract (`has_satellite_probability`);
        # any other sequence carrying an inherited buffer is unsafe unless it
        # proves the same contract.  This prevents a new satellite field being
        # silently dropped by the old 20-column layout.  Rows packed with the
        # versioned assessment contract (`hsd-assessment-feature-row-v2`) do
        # carry the probability columns, so they opt in through their buffer.
        if isinstance(features, NativeSnapshotFeatures):
            self._has_satellite_probability = bool(
                getattr(features, "has_satellite_probability", False)
            )
        elif isinstance(features, NativeFeatureList):
            self._has_satellite_probability = any(
                feature.horizontal_cloud_probability is not None
                or feature.cloud_probability_given_cloud is not None
                for feature in features
            )
        elif self._native_buffer is None:
            self._has_satellite_probability = any(
                feature.horizontal_cloud_probability is not None
                or feature.cloud_probability_given_cloud is not None
                for feature in features
            )
        else:
            self._has_satellite_probability = bool(
                getattr(features, "has_satellite_probability", True)
            )
        # v2 rows carry the probability columns, the 2 m surface pair and an
        # observer-elevation aware metrics ABI, so satellite features and
        # elevated observers no longer force the Python path.  Legacy 20-field
        # rows keep the old contract: no probability columns, sea-level
        # geometry, elevation 0 only.
        self._native_layout_v2 = (
            self._native_buffer is not None
            and int(getattr(self._native_buffer, "field_count", FIELD_COUNT))
            == FEATURE_ROW_V2_FIELD_COUNT
        )
        # The path-clearance / boundary-context kernels still consume legacy
        # 20-column rows (no probability columns, sea-level geometry).  Keep
        # them off for v2 buffers until their own v2 ABI lands; the Python
        # implementations remain the authoritative semantics there.
        self._native_scoring_enabled = (
            not self._native_layout_v2
            and abs(self.observer_elevation_m) < 1e-6
            and not self._has_satellite_probability
        )
        self._positions: Dict[int, int] = {}
        self._cloud_presence: List[float] = []
        self._explicit_presence: List[float] = []
        self._liquid_presence: List[float] = []
        self._ice_presence: List[float] = []
        self._cloud_opacity: List[float] = []
        self._blocker_opacity: List[float] = []
        self._lightpath_opacity: List[float] = []
        self._cloud_base: List[float] = []
        self._apparent_top: List[float] = []
        self._native_by_bearing: Dict[int, np.ndarray] = {}
        self._native_blockers_by_bearing: Dict[int, np.ndarray] = {}
        self._native_by_pressure_bearing: Dict[int, np.ndarray] = {}
        self._native_distances: Dict[bool, Dict[int, np.ndarray]] = {False: {}, True: {}}
        self._native_pressure_distances: Dict[int, np.ndarray] = {}
        # The legacy feature metrics use sea-level-relative geometry, so keep
        # elevated observer scoring on the Python path there; v2 metrics take
        # the observer height explicitly.  The same split applies to satellite
        # probability columns: v2 rows carry them, legacy rows cannot.
        self._native_mode = (
            self._native_buffer is not None
            and (self._native_layout_v2 or abs(self.observer_elevation_m) < 1e-6)
            and (self._native_layout_v2 or not self._has_satellite_probability)
            and callable(getattr(features, "feature_at", None))
            and native_available()
        )
        if self._native_mode:
            try:
                self._initialize_native(diagnostic_radius_km)
                return
            except Exception:
                if current_compute_backend() == "gpu":
                    raise
                self._native_mode = False
                self._native_scoring_enabled = False
                self._cloud_presence = []
                self._explicit_presence = []
                self._liquid_presence = []
                self._ice_presence = []
                self._cloud_opacity = []
                self._blocker_opacity = []
                self._lightpath_opacity = []
                self._cloud_base = []
                self._apparent_top = []

        self._positions = {id(feature): position for position, feature in enumerate(features)}
        for feature in features:
            fraction = cloud_fraction_score(feature)
            condensate = condensate_presence_score(feature)
            horizontal_probability = feature.horizontal_cloud_probability
            conditional_probability = feature.cloud_probability_given_cloud
            if feature.distance_km <= diagnostic_radius_km:
                rh_presence = cloud_presence_score(feature.rh_percent)
                if horizontal_probability is not None:
                    explicit_presence = clamp(float(horizontal_probability))
                    cloud_presence = clamp(
                        float(conditional_probability)
                        if conditional_probability is not None
                        else float(horizontal_probability)
                    )
                elif fraction is not None:
                    explicit_presence = (
                        fraction
                        if condensate is None
                        else clamp(fraction * (0.70 + 0.30 * condensate))
                    )
                else:
                    explicit_presence = condensate
                cloud_presence = (
                    rh_presence
                    if explicit_presence is None
                    else clamp(explicit_presence)
                )
                liquid_presence = condensate_component_score(feature.cloud_liquid_kg_kg)
                ice_presence = condensate_component_score(feature.cloud_ice_kg_kg)
            else:
                explicit_presence = None
                cloud_presence = 0.0
                liquid_presence = 0.0
                ice_presence = 0.0
            if horizontal_probability is not None:
                explicit_opacity = feature_cloud_opacity_score(feature)
            elif fraction is not None:
                opacity_material = condensate if condensate is not None else 0.5
                explicit_opacity: Optional[float] = clamp(
                    fraction * (0.35 + 0.65 * opacity_material)
                )
            else:
                explicit_opacity = condensate
            rh_opacity = cloud_opacity_score(feature.rh_percent)
            cloud_opacity = (
                rh_opacity
                if explicit_opacity is None
                else (
                    0.0
                    if fraction is not None and fraction <= 0.0
                    else clamp(0.22 * rh_opacity + 0.78 * explicit_opacity)
                )
            )
            blocker_opacity = (
                blocker_opacity_score(feature.rh_percent)
                if fraction is None and condensate is None
                else cloud_opacity
            )
            layer_factor = (
                1.0 if feature.pressure_hpa >= 700 else 0.82 if feature.pressure_hpa >= 500 else 0.58
            )
            self._cloud_presence.append(cloud_presence)
            self._explicit_presence.append(explicit_presence or 0.0)
            self._liquid_presence.append(liquid_presence)
            self._ice_presence.append(ice_presence)
            self._cloud_opacity.append(cloud_opacity)
            self._blocker_opacity.append(blocker_opacity)
            self._lightpath_opacity.append(clamp(blocker_opacity * layer_factor))
            self._cloud_base.append(
                estimated_cloud_base_m(feature, self.observer_elevation_m)
            )
            self._apparent_top.append(
                apparent_cloud_elevation_deg(
                    feature.distance_km,
                    feature.geopotential_height_m,
                    self.observer_elevation_m,
                )
            )
            key = int((feature.bearing_deg % 360.0) // bearing_bin_deg) % self.bin_count
            self.by_bearing.setdefault(key, []).append(feature)
            self.by_pressure_bearing.setdefault((feature.pressure_hpa, key), []).append(feature)
            if blocker_opacity > 0.12:
                self.blockers_by_bearing.setdefault(key, []).append(feature)
        for rows in (self.by_bearing, self.blockers_by_bearing):
            for values in rows.values():
                values.sort(key=lambda feature: feature.distance_km)
        self._distances = {
            False: {key: [feature.distance_km for feature in values] for key, values in self.by_bearing.items()},
            True: {key: [feature.distance_km for feature in values] for key, values in self.blockers_by_bearing.items()},
        }
        for values in self.by_pressure_bearing.values():
            values.sort(key=lambda feature: feature.distance_km)
        self._pressure_distances = {
            key: [feature.distance_km for feature in values]
            for key, values in self.by_pressure_bearing.items()
        }

    @staticmethod
    def _group_native_positions(
        positions: np.ndarray,
        keys: np.ndarray,
        distances: np.ndarray,
    ) -> tuple[Dict[int, np.ndarray], Dict[int, np.ndarray]]:
        if positions.size == 0:
            return {}, {}
        order = np.lexsort((positions, distances[positions], keys[positions]))
        sorted_positions = positions[order]
        sorted_keys = keys[sorted_positions]
        unique_keys, starts = np.unique(sorted_keys, return_index=True)
        ends = np.append(starts[1:], len(sorted_positions))
        groups = {
            int(key): sorted_positions[start:end]
            for key, start, end in zip(unique_keys, starts, ends)
        }
        group_distances = {
            key: distances[values]
            for key, values in groups.items()
        }
        return groups, group_distances

    def _initialize_native(self, diagnostic_radius_km: float) -> None:
        assert self._native_buffer is not None
        rows = self._native_buffer._storage.reshape(
            (-1, int(getattr(self._native_buffer, "field_count", FIELD_COUNT)))
        )
        metrics = native_feature_metrics(
            self._native_buffer,
            diagnostic_radius_km,
            self.observer_elevation_m,
        )
        self._cloud_presence = metrics[:, 0]
        self._explicit_presence = metrics[:, 1]
        self._liquid_presence = metrics[:, 2]
        self._ice_presence = metrics[:, 3]
        self._cloud_opacity = metrics[:, 4]
        self._blocker_opacity = metrics[:, 5]
        self._lightpath_opacity = metrics[:, 6]
        self._cloud_base = metrics[:, 7]
        self._apparent_top = metrics[:, 8]
        distances = rows[:, 2]
        bearing_keys = (
            np.floor(np.mod(rows[:, 3], 360.0) / self.bearing_bin_deg).astype(np.int32)
            % self.bin_count
        )
        positions = np.arange(len(rows), dtype=np.int64)
        self._native_by_bearing, self._native_distances[False] = self._group_native_positions(
            positions, bearing_keys, distances
        )
        blocker_positions = positions[metrics[:, 5] > 0.12]
        self._native_blockers_by_bearing, self._native_distances[True] = self._group_native_positions(
            blocker_positions, bearing_keys, distances
        )
        pressure_keys = rows[:, 4].astype(np.int32) * self.bin_count + bearing_keys
        (
            self._native_by_pressure_bearing,
            self._native_pressure_distances,
        ) = self._group_native_positions(positions, pressure_keys, distances)

    def _materialize_positions(self, positions: Sequence[int]) -> List[GridCellFeature]:
        feature_at = getattr(self._features, "feature_at")
        result: List[GridCellFeature] = []
        for position_value in positions:
            position = int(position_value)
            feature = feature_at(position)
            self._positions[id(feature)] = position
            result.append(feature)
        return result

    def _matching_keys(self, windows: Sequence[Tuple[float, float]]) -> List[int]:
        keys: set[int] = set()
        half_bin = self.bearing_bin_deg * 0.5
        for bearing, tolerance in windows:
            base_key = int((bearing % 360.0) // self.bearing_bin_deg) % self.bin_count
            radius = int(math.ceil((tolerance + half_bin) / self.bearing_bin_deg)) + 1
            for offset in range(-radius, radius + 1):
                key = (base_key + offset) % self.bin_count
                center = (key + 0.5) * self.bearing_bin_deg
                if angular_difference_deg(center, bearing) <= tolerance + half_bin:
                    keys.add(key)
        return sorted(keys)

    def query(
        self,
        windows: Sequence[Tuple[float, float]],
        min_distance_km: float = 0.0,
        max_distance_km: float = float("inf"),
        blockers_only: bool = False,
    ) -> List[GridCellFeature]:
        if self._native_mode:
            source = self._native_blockers_by_bearing if blockers_only else self._native_by_bearing
            distances = self._native_distances[blockers_only]
            positions: List[int] = []
            for key in self._matching_keys(windows):
                values = source.get(key)
                if values is None or values.size == 0:
                    continue
                key_distances = distances[key]
                start = int(np.searchsorted(key_distances, min_distance_km, side="left"))
                end = int(np.searchsorted(key_distances, max_distance_km, side="right"))
                positions.extend(values[start:end])
            return self._materialize_positions(positions)
        source = self.blockers_by_bearing if blockers_only else self.by_bearing
        distances = self._distances[blockers_only]
        result: List[GridCellFeature] = []
        for key in self._matching_keys(windows):
            values = source.get(key)
            if not values:
                continue
            start = bisect_left(distances[key], min_distance_km)
            end = bisect_right(distances[key], max_distance_km)
            result.extend(values[start:end])
        return result

    def query_pressure(
        self,
        windows: Sequence[Tuple[float, float]],
        pressure_hpa: int,
        min_distance_km: float = 0.0,
        max_distance_km: float = float("inf"),
    ) -> List[GridCellFeature]:
        if self._native_mode:
            positions: List[int] = []
            for bearing_key in self._matching_keys(windows):
                key = pressure_hpa * self.bin_count + bearing_key
                values = self._native_by_pressure_bearing.get(key)
                if values is None or values.size == 0:
                    continue
                distances = self._native_pressure_distances[key]
                start = int(np.searchsorted(distances, min_distance_km, side="left"))
                end = int(np.searchsorted(distances, max_distance_km, side="right"))
                positions.extend(values[start:end])
            return self._materialize_positions(positions)
        result: List[GridCellFeature] = []
        for bearing_key in self._matching_keys(windows):
            key = (pressure_hpa, bearing_key)
            values = self.by_pressure_bearing.get(key)
            if not values:
                continue
            distances = self._pressure_distances[key]
            start = bisect_left(distances, min_distance_km)
            end = bisect_right(distances, max_distance_km)
            result.extend(values[start:end])
        return result

    def _derived(self, values: Sequence[float], feature: GridCellFeature, fallback) -> float:
        position = self._positions.get(id(feature))
        return values[position] if position is not None else fallback(feature)

    def cloud_opacity(self, feature: GridCellFeature) -> float:
        return self._derived(self._cloud_opacity, feature, feature_cloud_opacity_score)

    def blocker_opacity(self, feature: GridCellFeature) -> float:
        return self._derived(self._blocker_opacity, feature, feature_blocker_opacity_score)

    def lightpath_opacity(self, feature: GridCellFeature) -> float:
        return self._derived(self._lightpath_opacity, feature, lightpath_blocker_opacity)

    def cloud_base(self, feature: GridCellFeature) -> float:
        return self._derived(
            self._cloud_base,
            feature,
            lambda cell: estimated_cloud_base_m(cell, self.observer_elevation_m),
        )

    def apparent_top(self, feature: GridCellFeature) -> float:
        return self._derived(
            self._apparent_top,
            feature,
            lambda item: apparent_cloud_elevation_deg(
                item.distance_km,
                item.geopotential_height_m,
                self.observer_elevation_m,
            ),
        )

    def path_clearance_weights(
        self,
        candidates: Sequence[GridCellFeature],
        solar_elevation_deg: float,
        solar_azimuth_deg: float,
    ) -> Optional[List[float]]:
        if not candidates or not self._native_scoring_enabled or not native_available():
            return None

        positions = [self._positions.get(id(feature)) for feature in candidates]
        if any(position is None for position in positions):
            return None
        try:
            if self._native_buffer is None:
                self._native_buffer = NativeFeatureBuffer(self._features)
            return native_path_clearance_weights(
                self._native_buffer,
                [position for position in positions if position is not None],
                solar_elevation_deg,
                solar_azimuth_deg,
            )
        except Exception:
            self._native_scoring_enabled = False
            return None

    def candidate_rows(
        self,
        solar_elevation_deg: float,
        solar_azimuth_deg: float,
    ) -> Optional[List[tuple[GridCellFeature, float, float, float, float, float, float]]]:
        if self._native_layout_v2:
            # The candidate prefilter ABI still consumes legacy 20-column rows
            # and scores feasibility against the fixed layer-depth table with
            # sea-level sight geometry.  v2 buffers stay on the Python selector
            # until the prefilter gains its own v2 kernel, so satellite
            # probabilities and observer elevation cannot change selection
            # silently.
            return None
        if not self._native_mode or self._native_buffer is None or not native_available():
            return None
        try:
            rows = native_candidate_prefilter(
                self._native_buffer,
                solar_elevation_deg,
                solar_azimuth_deg,
            )
        except Exception:
            if current_compute_backend() == "gpu":
                raise
            return None
        positions = rows[:, 0].astype(np.int64, copy=False)
        features = self._materialize_positions(positions)
        return [
            (
                feature,
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
                float(row[6]),
            )
            for feature, row in zip(features, rows)
        ]

    def boundary_rows(
        self,
        solar_azimuth_deg: float,
    ) -> Optional[List[tuple[GridCellFeature, float, float]]]:
        if self._native_layout_v2:
            # Same reason as candidate_rows: the legacy boundary prefilter
            # reads opacity from the 20-column layout and its output is
            # trusted verbatim by the caller, so a v2 buffer must not reach it.
            return None
        if not self._native_mode or self._native_buffer is None or not native_available():
            return None
        try:
            rows = native_boundary_prefilter(self._native_buffer, solar_azimuth_deg)
        except Exception:
            if current_compute_backend() == "gpu":
                raise
            return None
        positions = rows[:, 0].astype(np.int64, copy=False)
        features = self._materialize_positions(positions)
        return [
            (feature, float(row[1]), float(row[2]))
            for feature, row in zip(features, rows)
        ]

    def boundary_clearance_weights(
        self,
        targets: Sequence[GridCellFeature],
        target_heights_m: Sequence[float],
        solar_elevation_deg: float,
        solar_azimuth_deg: float,
    ) -> Optional[List[float]]:
        if not targets or not self._native_scoring_enabled or not native_available():
            return None
        positions = [self._positions.get(id(feature)) for feature in targets]
        if any(position is None for position in positions):
            return None
        try:
            if self._native_buffer is None:
                self._native_buffer = NativeFeatureBuffer(self._features)
            return native_boundary_clearance_weights(
                self._native_buffer,
                [position for position in positions if position is not None],
                target_heights_m,
                solar_elevation_deg,
                solar_azimuth_deg,
            )
        except Exception:
            self._native_scoring_enabled = False
            return None

    def boundary_context_scores(
        self,
        targets: Sequence[GridCellFeature],
        solar_azimuth_deg: float,
    ) -> Optional[List[tuple[float, float]]]:
        if not targets or not self._native_scoring_enabled or not native_available():
            return None
        positions = [self._positions.get(id(feature)) for feature in targets]
        if any(position is None for position in positions):
            return None
        try:
            if self._native_buffer is None:
                self._native_buffer = NativeFeatureBuffer(self._features)
            return native_boundary_context_scores(
                self._native_buffer,
                [position for position in positions if position is not None],
                solar_azimuth_deg,
            )
        except Exception:
            self._native_scoring_enabled = False
            return None


def indexed_features(
    snapshot: DomainSnapshot,
    feature_index: Optional[FeatureSpatialIndex],
    windows: Sequence[Tuple[float, float]],
    min_distance_km: float = 0.0,
    max_distance_km: float = float("inf"),
    blockers_only: bool = False,
) -> Sequence[GridCellFeature]:
    if feature_index is None:
        return snapshot.features
    return feature_index.query(windows, min_distance_km, max_distance_km, blockers_only)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def smooth_gate(value: float, low: float, high: float) -> float:
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    ratio = (value - low) / (high - low)
    return ratio * ratio * (3.0 - 2.0 * ratio)


def atmospheric_clarity_score(aod550: float) -> float:
    """Uniform 550 nm atmospheric clarity shared by scoring backends."""
    return clamp(0.50 + 0.50 * math.exp(-6.0 * max(0.0, aod550)))


def pressure_weight(pressure_hpa: int) -> float:
    if pressure_hpa <= 400:
        return 1.0
    if pressure_hpa <= 500:
        return 0.92
    if pressure_hpa <= 600:
        return 0.8
    if pressure_hpa <= 700:
        return 0.66
    return 0.35


def distance_weight(distance_km: float) -> float:
    return clamp(1.0 - abs(distance_km - 220.0) / 220.0)


def illumination_feasibility(cloud_height_m: float, required_height_m: float) -> float:
    height_margin_m = cloud_height_m - required_height_m
    return clamp((height_margin_m + 1200.0) / 3600.0)


def apparent_cloud_elevation_deg(
    distance_km: float,
    cloud_height_m: float,
    observer_elevation_m: float = 0.0,
) -> float:
    distance_m = max(1.0, distance_km * 1000.0)
    curvature_drop_m = curvature_drop_at_distance_m(distance_km)
    visible_height_m = cloud_height_m - observer_elevation_m - curvature_drop_m
    return math.degrees(math.atan2(visible_height_m, distance_m))


def curvature_drop_at_distance_m(distance_km: float) -> float:
    return (distance_km * distance_km * 1000.0) / (2.0 * EARTH_RADIUS_KM)


def sightline_height_at_distance_m(
    target_distance_km: float,
    target_height_m: float,
    sample_distance_km: float,
    observer_elevation_m: float = 0.0,
) -> float:
    apparent = math.radians(
        apparent_cloud_elevation_deg(
            target_distance_km,
            target_height_m,
            observer_elevation_m,
        )
    )
    relative_height = (
        math.tan(apparent) * sample_distance_km * 1000.0
        + curvature_drop_at_distance_m(sample_distance_km)
    )
    return observer_elevation_m + relative_height


def solar_ray_height_at_distance_m(
    target_distance_km: float,
    target_height_m: float,
    sample_distance_km: float,
    solar_elevation_deg: float,
) -> float:
    """Height of the sunward ray above MSL at one sample distance.

    Target-local tangent frame, matching the native national.rs kernel: the
    ray leaves the target base at the target-local incidence angle and only
    the delta segment curves away from it. The observer->target tilt is
    already inside ``+ target_distance_km / EARTH_RADIUS_KM``; adding
    ``drop(s) - drop(D)`` on top double-counted ``D * delta / R`` of
    curvature and grew linearly with the observer-target distance.
    """
    distance_delta_m = (sample_distance_km - target_distance_km) * 1000.0
    target_local_elevation_rad = math.radians(
        solar_illumination_elevation_deg(solar_elevation_deg)
    ) + target_distance_km / EARTH_RADIUS_KM
    return (
        target_height_m
        + math.tan(target_local_elevation_rad) * distance_delta_m
        + distance_delta_m * distance_delta_m / (2.0 * EARTH_RADIUS_KM * 1000.0)
    )


def vertical_ray_intersection_score(ray_height_m: float, cloud_base_m: float, cloud_top_m: float, tolerance_m: float = 650.0) -> float:
    if ray_height_m < cloud_base_m - tolerance_m or ray_height_m > cloud_top_m + tolerance_m:
        return 0.0
    if cloud_base_m <= ray_height_m <= cloud_top_m:
        return 1.0
    edge_gap = min(abs(ray_height_m - cloud_base_m), abs(ray_height_m - cloud_top_m))
    return clamp(1.0 - edge_gap / tolerance_m)


def line_of_sight_weight(apparent_elevation_deg: float) -> float:
    return clamp((apparent_elevation_deg - 0.1) / 1.4)


def horizon_proximity_weight(apparent_elevation_deg: float) -> float:
    if apparent_elevation_deg <= 0.1:
        return 0.0
    if apparent_elevation_deg < 1.5:
        return clamp((apparent_elevation_deg - 0.1) / 1.4)
    if apparent_elevation_deg <= 4.0:
        return 1.0
    if apparent_elevation_deg < 12.0:
        return 1.0 - 0.5 * (apparent_elevation_deg - 4.0) / 8.0
    return clamp(0.5 - 0.25 * (apparent_elevation_deg - 12.0) / 12.0, low=0.25)


def aerosol_transmission_weight(aod550: float, apparent_elevation_deg: float) -> float:
    elevation_rad = math.radians(max(2.0, apparent_elevation_deg))
    air_mass = min(12.0, 1.0 / max(0.08, math.sin(elevation_rad)))
    transmission = math.exp(-0.45 * aod550 * air_mass)
    return clamp(0.50 + 0.50 * transmission)


def aerosol_extinction_coefficient(
    aod550: float,
    height_m: float,
    scale_height_m: float = AEROSOL_SCALE_HEIGHT_M,
) -> float:
    height = max(0.0, height_m)
    scale_height = max(1.0, scale_height_m)
    return (aod550 / scale_height) * math.exp(-height / scale_height)


def observer_aerosol_optical_depth(
    aod550: float,
    target_distance_km: float,
    target_height_m: float,
    scale_height_m: float = AEROSOL_SCALE_HEIGHT_M,
    observer_elevation_m: float = 0.0,
) -> float:
    if aod550 <= 0.0 or target_distance_km <= 0.0:
        return 0.0
    steps = max(16, min(96, int(target_distance_km / 10.0)))
    tau = 0.0
    previous_distance = 0.0
    # The CAMS value is the column above the local model surface.  Integrate
    # that column in AGL coordinates from the explicit observer station.
    previous_height = observer_elevation_m
    previous_alpha = aerosol_extinction_coefficient(
        aod550,
        max(0.0, previous_height - observer_elevation_m),
        scale_height_m,
    )
    for index in range(1, steps + 1):
        distance = target_distance_km * index / steps
        height = sightline_height_at_distance_m(
            target_distance_km,
            target_height_m,
            distance,
            observer_elevation_m,
        )
        alpha = aerosol_extinction_coefficient(
            aod550,
            max(0.0, height - observer_elevation_m),
            scale_height_m,
        )
        dx_m = (distance - previous_distance) * 1000.0
        dz_m = height - previous_height
        ds_m = math.hypot(dx_m, dz_m)
        tau += 0.5 * (alpha + previous_alpha) * ds_m
        previous_distance = distance
        previous_height = height
        previous_alpha = alpha
    return tau


def solar_inflow_aerosol_optical_depth(
    aod550: float,
    target_distance_km: float,
    target_height_m: float,
    solar_elevation_deg: float,
    max_distance_km: float = MAX_SOLAR_INFLOW_AEROSOL_DISTANCE_KM,
    scale_height_m: float = AEROSOL_SCALE_HEIGHT_M,
    observer_elevation_m: float = 0.0,
) -> float:
    if aod550 <= 0.0:
        return 0.0
    stop_distance = max(target_distance_km + 20.0, max_distance_km)
    steps = max(48, min(180, int((stop_distance - target_distance_km) / 15.0)))
    # The CAMS value is the column above the local model surface: integrate
    # the incoming leg in the same observer-relative AGL coordinates as the
    # return leg, so one physical point gets one extinction value on both
    # legs. (The geometric ground test below stays in MSL terms.)
    tau = 0.0
    previous_distance = target_distance_km
    previous_height = target_height_m
    previous_alpha = aerosol_extinction_coefficient(
        aod550,
        max(0.0, previous_height - observer_elevation_m),
        scale_height_m,
    )
    for index in range(1, steps + 1):
        distance = target_distance_km + (stop_distance - target_distance_km) * index / steps
        height = solar_ray_height_at_distance_m(target_distance_km, target_height_m, distance, solar_elevation_deg)
        if height <= 0.0:
            fraction = previous_height / max(previous_height - height, 1.0)
            ground_distance = previous_distance + (distance - previous_distance) * clamp(fraction)
            dx_m = (ground_distance - previous_distance) * 1000.0
            dz_m = -previous_height
            ds_m = math.hypot(dx_m, dz_m)
            ground_alpha = aerosol_extinction_coefficient(aod550, 0.0, scale_height_m)
            tau += 0.5 * (previous_alpha + ground_alpha) * ds_m
            break
        alpha = aerosol_extinction_coefficient(
            aod550,
            max(0.0, height - observer_elevation_m),
            scale_height_m,
        )
        dx_m = (distance - previous_distance) * 1000.0
        dz_m = height - previous_height
        ds_m = math.hypot(dx_m, dz_m)
        tau += 0.5 * (alpha + previous_alpha) * ds_m
        previous_distance = distance
        previous_height = height
        previous_alpha = alpha
    return tau


def aerosol_lightpath_transmission_weight(
    aod550: float,
    target_distance_km: float,
    target_height_m: float,
    apparent_elevation_deg: float,
    solar_elevation_deg: float,
    observer_elevation_m: float = 0.0,
) -> float:
    weights = aerosol_lightpath_transmission_samples(
        aod550,
        target_distance_km,
        target_height_m,
        apparent_elevation_deg,
        solar_elevation_deg,
        observer_elevation_m=observer_elevation_m,
    )
    return aerosol_transmission_weight_from_samples(weights)


def aerosol_transmission_weight_from_samples(weights: Sequence[float]) -> float:
    if not weights:
        return 0.0
    spread = max(weights) - min(weights)
    return clamp(mean(weights) - 0.15 * spread)


def aerosol_lightpath_transmission_samples(
    aod550: float,
    target_distance_km: float,
    target_height_m: float,
    apparent_elevation_deg: float,
    solar_elevation_deg: float,
    observer_aod550: Optional[float] = None,
    inflow_aod550: Optional[float] = None,
    observer_elevation_m: float = 0.0,
) -> List[float]:
    global _native_aerosol_enabled
    legacy = aerosol_transmission_weight(aod550, apparent_elevation_deg)
    observer_aod = aod550 if observer_aod550 is None else observer_aod550
    inflow_aod = aod550 if inflow_aod550 is None else inflow_aod550
    if _native_aerosol_enabled and native_available():
        try:
            return native_aerosol_transmission_samples(
                aod550,
                observer_aod,
                inflow_aod,
                target_distance_km,
                target_height_m,
                apparent_elevation_deg,
                solar_elevation_deg,
                observer_elevation_m,
            )
        except Exception:
            _native_aerosol_enabled = False
    weights: List[float] = []
    above_fraction = math.exp(-max(0.0, target_height_m - observer_elevation_m) / 8500.0)
    below_fraction = 1.0 - above_fraction
    app_elev_rad = math.radians(max(2.0, apparent_elevation_deg))
    obs_airmass = below_fraction / max(0.08, math.sin(app_elev_rad))
    sol_rad = math.radians(solar_elevation_deg)
    if sol_rad > 0.0:
        total_am = 1.0 / max(0.05, math.sin(sol_rad))
        inflow_airmass = total_am * above_fraction
    else:
        depression_deg = min(20.0, -solar_elevation_deg)
        inflow_airmass = 50.0 / (depression_deg + 2.0) * above_fraction
    total_am_sum = obs_airmass + inflow_airmass
    observer_weight = clamp(obs_airmass / max(1.0, total_am_sum), low=0.20, high=0.60) if total_am_sum > 0.0 else 0.30
    inflow_weight = 1.0 - observer_weight
    for scale_height_m in AEROSOL_SCALE_HEIGHTS_M:
        observer_tau = observer_aerosol_optical_depth(
            observer_aod,
            target_distance_km,
            target_height_m,
            scale_height_m,
            observer_elevation_m,
        )
        inflow_tau = solar_inflow_aerosol_optical_depth(
            inflow_aod,
            target_distance_km,
            target_height_m,
            solar_elevation_deg,
            scale_height_m=scale_height_m,
            observer_elevation_m=observer_elevation_m,
        )
        combined_tau = observer_weight * observer_tau + inflow_weight * inflow_tau
        transmission = math.exp(-LEGACY_AEROSOL_EFFECTIVE_COEFFICIENT * min(12.0, combined_tau))
        weights.append(clamp(0.35 + 0.65 * transmission, high=legacy))
    return weights


def path_aod_components(
    aerosol: AerosolReading,
    target_lat: float,
    target_lon: float,
    target_distance_km: float,
    target_bearing_deg: float,
    solar_azimuth_deg: float,
) -> Tuple[float, float, float]:
    if not aerosol.path_samples or aerosol.observer_lat is None or aerosol.observer_lon is None:
        return aerosol.aod550, aerosol.aod550, 0.0

    observer_rows: List[Tuple[float, float]] = []
    observer_width_km = max(70.0, min(160.0, 0.18 * target_distance_km))
    for sample in aerosol.path_samples:
        delta_rad = math.radians(angular_difference_deg(sample.bearing_deg, target_bearing_deg))
        along_km = sample.distance_km * math.cos(delta_rad)
        cross_km = abs(sample.distance_km * math.sin(delta_rad))
        if -30.0 <= along_km <= target_distance_km + 50.0 and cross_km <= observer_width_km:
            observer_rows.append((max(0.0, along_km), sample.aod550))

    target = GeoPoint(lat=target_lat, lon=target_lon)
    inflow_rows: List[Tuple[float, float]] = []
    max_inflow_km = max(200.0, MAX_SOLAR_INFLOW_AEROSOL_DISTANCE_KM - target_distance_km)
    for sample in aerosol.path_samples:
        sample_point = GeoPoint(lat=sample.lat, lon=sample.lon)
        distance_km = haversine_km(target, sample_point)
        if distance_km > max_inflow_km + 100.0:
            continue
        bearing_deg = initial_bearing_deg(target, sample_point) if distance_km > 1.0 else solar_azimuth_deg
        delta_rad = math.radians(angular_difference_deg(bearing_deg, solar_azimuth_deg))
        along_km = distance_km * math.cos(delta_rad)
        cross_km = abs(distance_km * math.sin(delta_rad))
        if -30.0 <= along_km <= max_inflow_km and cross_km <= 140.0:
            inflow_rows.append((max(0.0, along_km), sample.aod550))

    observer_aod, observer_variability = binned_path_aod(observer_rows, aerosol.aod550)
    inflow_aod, inflow_variability = binned_path_aod(inflow_rows, aerosol.aod550)
    variability = clamp(max(observer_variability, inflow_variability) / 0.45)
    return observer_aod, inflow_aod, variability


def binned_path_aod(
    rows: Sequence[Tuple[float, float]],
    fallback_aod550: float,
    bin_width_km: float = 120.0,
) -> Tuple[float, float]:
    if not rows:
        return fallback_aod550, 0.0
    bins: Dict[int, List[float]] = {}
    for distance_km, aod550 in rows:
        bins.setdefault(int(distance_km // bin_width_km), []).append(aod550)
    bin_means = [mean(values) for _, values in sorted(bins.items())]
    return mean(bin_means), max(bin_means) - min(bin_means)


def solar_elevation_weight(solar_elevation_deg: float) -> float:
    if solar_elevation_deg >= 8.0:
        return clamp(1.0 - (solar_elevation_deg - 8.0) / 8.0)
    if solar_elevation_deg >= -8.0:
        return 1.0
    if solar_elevation_deg >= -14.0:
        return clamp((solar_elevation_deg + 14.0) / 6.0)
    return 0.0


def visibility_gate(visibility_score: float) -> float:
    return clamp((visibility_score - 0.15) / 0.55)


def low_cloud_penalty(
    snapshot: DomainSnapshot,
    solar_azimuth_deg: float,
    feature_index: Optional[FeatureSpatialIndex] = None,
) -> float:
    penalties: List[float] = []
    for feature in indexed_features(
        snapshot,
        feature_index,
        ((solar_azimuth_deg, 70.0),),
        max_distance_km=120.0,
        blockers_only=True,
    ):
        if feature.pressure_hpa < 750:
            continue
        if feature.distance_km > 120.0:
            continue
        sector_delta = angular_difference_deg(feature.bearing_deg, solar_azimuth_deg)
        if sector_delta > 70.0:
            continue
        penalties.append(feature_blocker_opacity_score(feature))
    if not penalties:
        return 0.0
    return clamp(mean(penalties))


def surface_weather_penalty(
    snapshot: DomainSnapshot,
    feature_index: Optional[FeatureSpatialIndex] = None,
) -> tuple[float, Optional[float], Optional[float]]:
    points: Dict[tuple[float, float], GridCellFeature] = {}
    features = (
        feature_index.query(((0.0, 180.0),), max_distance_km=80.0)
        if feature_index is not None
        else snapshot.features
    )
    for feature in features:
        if feature.distance_km > 80.0:
            continue
        if feature.surface_visibility_m is None and feature.precip_rate_kg_m2_s is None:
            continue
        key = (round(feature.lat, 4), round(feature.lon, 4))
        current = points.get(key)
        if current is None or feature.distance_km < current.distance_km:
            points[key] = feature
    nearest = sorted(points.values(), key=lambda feature: feature.distance_km)[:12]
    if not nearest:
        return 0.0, None, None
    visibility_values = [feature.surface_visibility_m for feature in nearest if feature.surface_visibility_m is not None]
    precipitation_values = [feature.precip_rate_kg_m2_s for feature in nearest if feature.precip_rate_kg_m2_s is not None]
    visibility_m = mean(visibility_values) if visibility_values else None
    precip_rate = mean(precipitation_values) if precipitation_values else None
    visibility_penalty = clamp((9000.0 - visibility_m) / 8000.0) if visibility_m is not None else 0.0
    precipitation_penalty = clamp((precip_rate or 0.0) / 0.0008)
    return clamp(max(0.85 * visibility_penalty, precipitation_penalty)), visibility_m, precip_rate


def cloud_opacity_score(rh_percent: float) -> float:
    return clamp((rh_percent - 82.0) / 16.0)


def cloud_presence_score(rh_percent: float) -> float:
    return clamp((rh_percent - 45.0) / 45.0)


def condensate_presence_score(feature: GridCellFeature) -> Optional[float]:
    if feature.cloud_liquid_kg_kg is None and feature.cloud_ice_kg_kg is None:
        return None
    total = max(0.0, feature.cloud_liquid_kg_kg or 0.0) + max(0.0, feature.cloud_ice_kg_kg or 0.0)
    if total <= 0.0:
        return 0.0
    return clamp((math.log10(total) + 8.0) / 3.0)


def cloud_fraction_score(feature: GridCellFeature) -> Optional[float]:
    if feature.horizontal_cloud_probability is not None:
        return clamp(float(feature.horizontal_cloud_probability))
    if feature.cloud_fraction_percent is None:
        return None
    return clamp(feature.cloud_fraction_percent / 100.0)


def explicit_cloud_presence_score(feature: GridCellFeature) -> Optional[float]:
    if feature.horizontal_cloud_probability is not None:
        # Horizontal occupancy is already the complete footprint probability;
        # do not attenuate it by a conditional vertical profile or condensate.
        return clamp(float(feature.horizontal_cloud_probability))
    fraction = cloud_fraction_score(feature)
    condensate = condensate_presence_score(feature)
    if fraction is not None:
        if condensate is None:
            return fraction
        # Condensate describes material inside cloud, not grid-cell coverage.
        return clamp(fraction * (0.70 + 0.30 * condensate))
    return condensate


def feature_cloud_presence_score(feature: GridCellFeature) -> float:
    explicit = explicit_cloud_presence_score(feature)
    if explicit is None:
        return cloud_presence_score(feature.rh_percent)
    return clamp(explicit)


def feature_cloud_opacity_score(feature: GridCellFeature) -> float:
    if feature.horizontal_cloud_probability is not None:
        probability = clamp(float(feature.horizontal_cloud_probability))
        if probability <= 0.0:
            return 0.0
        return probability
    rh_opacity = cloud_opacity_score(feature.rh_percent)
    fraction = cloud_fraction_score(feature)
    condensate = condensate_presence_score(feature)
    if fraction is not None:
        material = condensate if condensate is not None else 0.5
        explicit = clamp(fraction * (0.35 + 0.65 * material))
    else:
        explicit = condensate
    if explicit is None:
        return rh_opacity
    # A valid fraction controls horizontal coverage. For a non-zero cloud
    # amount, RH can still describe optical thickness; explicit 0% is the
    # hard exception and cannot be revived by humidity.
    if fraction is not None:
        if fraction <= 0.0:
            return 0.0
        return clamp(0.22 * rh_opacity + 0.78 * explicit)
    if condensate is not None:
        return clamp(0.22 * rh_opacity + 0.78 * explicit)
    return rh_opacity


def feature_blocker_opacity_score(feature: GridCellFeature) -> float:
    explicit_opacity = feature_cloud_opacity_score(feature)
    if (
        feature.horizontal_cloud_probability is None
        and feature.cloud_fraction_percent is None
        and condensate_presence_score(feature) is None
    ):
        return blocker_opacity_score(feature.rh_percent)
    return explicit_opacity


def high_cloud_suitability_score(rh_percent: float) -> float:
    if rh_percent < 45.0:
        return 0.0
    if rh_percent < 65.0:
        return clamp((rh_percent - 45.0) / 20.0) * 0.70
    if rh_percent <= 88.0:
        return 0.86 + 0.14 * clamp((rh_percent - 65.0) / 23.0)
    if rh_percent <= 96.0:
        return 1.0
    return 0.92


def blocker_opacity_score(rh_percent: float) -> float:
    return clamp((rh_percent - 88.0) / 10.0)


def layer_name(pressure_hpa: int) -> str:
    if pressure_hpa <= 400:
        return "high"
    if pressure_hpa <= 700:
        return "mid"
    return "low"


def layer_label(layer: str) -> str:
    return {"high": "高云", "mid": "中云", "low": "低云"}.get(layer, layer)


LCL_BASE_SPREAD_M_PER_K = 125.0
LCL_BASE_NEAR_FIELD_KM = 120.0


def estimated_cloud_base_m(
    feature: GridCellFeature,
    terrain_asl_m: float | None = None,
) -> float:
    """Estimate the cloud base for one pressure-level feature.

    The legacy fixed layer-depth table (850 hPa -> 450 m) ignored the actual
    boundary-layer structure: coastal stratus bases sit 300-500 m above the
    ground while cumulus bases reach 1300-1800 m, so the same depth was
    systematically wrong in both directions. When the GFS 2 m slice is
    available and the cell is near enough that the observer elevation is a
    fair terrain proxy, the classic surface-parcel LCL
    (``base_agl = 125 m/K * (T - Td)``) replaces the table; otherwise the
    old estimate is returned unchanged.
    """
    layer_depth_m = {
        200: 2200.0,
        250: 2000.0,
        300: 1800.0,
        400: 1500.0,
        500: 1200.0,
        600: 900.0,
        700: 700.0,
        750: 550.0,
        800: 500.0,
        850: 450.0,
        925: 300.0,
    }.get(feature.pressure_hpa, 900.0)
    legacy_base_m = max(0.0, feature.geopotential_height_m - layer_depth_m)
    if terrain_asl_m is None or feature.distance_km > LCL_BASE_NEAR_FIELD_KM:
        return legacy_base_m
    temperature_k = feature.surface_temperature_k
    dewpoint_k = feature.surface_dewpoint_k
    if temperature_k is None or dewpoint_k is None:
        return legacy_base_m
    try:
        spread_k = float(temperature_k) - float(dewpoint_k)
    except (TypeError, ValueError):
        return legacy_base_m
    if not math.isfinite(spread_k) or spread_k < 0.0:
        return legacy_base_m
    lcl_base_m = float(terrain_asl_m) + LCL_BASE_SPREAD_M_PER_K * spread_k
    # The deck cannot sit above its own top level. An LCL above the level
    # means the surface parcel never reaches this deck (elevated cloud over
    # a dry boundary layer), so the legacy table stays in charge there; a
    # low LCL is exactly the stratus signal the fixed table got wrong.
    ceiling_m = max(0.0, feature.geopotential_height_m - 100.0)
    if lcl_base_m > ceiling_m:
        return legacy_base_m
    return max(0.0, min(ceiling_m, lcl_base_m))


def condensate_component_score(value: Optional[float]) -> float:
    if value is None or value <= 0.0:
        return 0.0
    return clamp((math.log10(value) + 8.0) / 3.0)


def estimated_column_cloud_bounds(
    layers: Dict[int, GridCellFeature],
    cloudy_layers: Sequence[GridCellFeature],
    threshold: float = 0.45,
    presence_scores: Optional[Dict[int, float]] = None,
) -> Tuple[float, float]:
    def presence(feature: GridCellFeature) -> float:
        if presence_scores is None:
            return feature_cloud_presence_score(feature)
        return presence_scores[id(feature)]

    ordered = sorted(layers.values(), key=lambda feature: feature.geopotential_height_m)
    cloudy = sorted(cloudy_layers, key=lambda feature: feature.geopotential_height_m)
    lowest = cloudy[0]
    highest = cloudy[-1]

    lower_dry = [
        feature
        for feature in ordered
        if feature.geopotential_height_m < lowest.geopotential_height_m
        and presence(feature) < threshold
    ]
    if lower_dry:
        lower = lower_dry[-1]
        base_m = interpolate_cloud_threshold_height(lower, lowest, threshold, presence_scores)
    else:
        base_m = estimated_cloud_base_m(lowest)

    upper_dry = [
        feature
        for feature in ordered
        if feature.geopotential_height_m > highest.geopotential_height_m
        and presence(feature) < threshold
    ]
    if upper_dry:
        top_m = interpolate_cloud_threshold_height(highest, upper_dry[0], threshold, presence_scores)
    else:
        top_m = highest.geopotential_height_m
    return max(0.0, base_m), max(base_m, top_m)


def interpolate_cloud_threshold_height(
    first: GridCellFeature,
    second: GridCellFeature,
    threshold: float,
    presence_scores: Optional[Dict[int, float]] = None,
) -> float:
    first_score = presence_scores[id(first)] if presence_scores is not None else feature_cloud_presence_score(first)
    second_score = presence_scores[id(second)] if presence_scores is not None else feature_cloud_presence_score(second)
    score_delta = second_score - first_score
    if abs(score_delta) < 1e-6:
        return 0.5 * (first.geopotential_height_m + second.geopotential_height_m)
    fraction = clamp((threshold - first_score) / score_delta)
    return first.geopotential_height_m + fraction * (
        second.geopotential_height_m - first.geopotential_height_m
    )


ColumnIndex = Dict[Tuple[float, float, float, float], CloudColumn]

_NATIVE_CLOUD_TYPES = {
    0: "弱云/晴空",
    1: "深厚云盾",
    2: "积雨云云砧",
    3: "层云/层积云",
    4: "高积云",
    5: "高层云",
    6: "卷云",
    7: "中云",
    8: "低云",
}
_NATIVE_PRESSURE_LEVELS = (200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925)


class NativeColumnLayers(Mapping[int, GridCellFeature]):
    def __init__(self, features, positions: Sequence[float]) -> None:
        self._features = features
        self._positions = {
            level: int(position)
            for level, position in zip(_NATIVE_PRESSURE_LEVELS, positions)
            if position >= 0.0
        }

    def __getitem__(self, pressure_hpa: int) -> GridCellFeature:
        return self._features.feature_at(self._positions[pressure_hpa])

    def __iter__(self) -> Iterator[int]:
        return iter(self._positions)

    def __len__(self) -> int:
        return len(self._positions)


def build_native_cloud_columns(
    snapshot: DomainSnapshot,
    feature_index: FeatureSpatialIndex,
    max_distance_km: float,
) -> ColumnIndex:
    assert feature_index._native_buffer is not None
    rows = native_aggregate_cloud_columns(feature_index._native_buffer, max_distance_km)
    columns: ColumnIndex = {}
    for row in rows:
        key = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
        columns[key] = CloudColumn(
            lat=float(row[4]),
            lon=float(row[5]),
            distance_km=float(row[6]),
            bearing_deg=float(row[7]),
            layers=NativeColumnLayers(snapshot.features, row[20:31]),
            high_presence=float(row[8]),
            mid_presence=float(row[9]),
            low_presence=float(row[10]),
            deep_cloud_risk=float(row[11]),
            thickness_risk=float(row[12]),
            cloud_type=_NATIVE_CLOUD_TYPES[int(row[13])],
            burnability_score=float(row[14]),
            cloud_base_m=float(row[15]),
            cloud_top_m=float(row[16]),
            liquid_presence=float(row[17]),
            ice_presence=float(row[18]),
            explicit_cloud_evidence=float(row[19]),
        )
    return columns


def column_key(feature: GridCellFeature) -> Tuple[float, float, float, float]:
    if (
        feature.source_lat is not None
        and feature.source_lon is not None
        and feature.source_distance_km is not None
        and feature.source_bearing_deg is not None
    ):
        return (
            feature.source_lat,
            feature.source_lon,
            feature.source_distance_km,
            feature.source_bearing_deg,
        )
    return (
        round(feature.lat, 4),
        round(feature.lon, 4),
        round(feature.distance_km, 1),
        round(feature.bearing_deg, 1),
    )


def build_cloud_columns(
    snapshot: DomainSnapshot,
    feature_index: Optional[FeatureSpatialIndex] = None,
    max_distance_km: float = float("inf"),
) -> ColumnIndex:
    if (
        feature_index is not None
        and feature_index._native_mode
        and not feature_index._native_layout_v2
    ):
        # Native column aggregation consumes legacy 20-column rows only; a v2
        # buffer handed to that ABI would be re-read at the wrong stride, so
        # those columns keep the Python aggregation (which stays authoritative
        # for satellite probability semantics anyway).
        try:
            return build_native_cloud_columns(snapshot, feature_index, max_distance_km)
        except Exception:
            feature_index._native_scoring_enabled = False
    grouped: Dict[Tuple[float, float, float, float], Dict[int, GridCellFeature]] = {}
    presence_scores: Dict[int, float] = {}
    explicit_scores: Dict[int, float] = {}
    horizontal_scores: Dict[int, float] = {}
    liquid_scores: Dict[int, float] = {}
    ice_scores: Dict[int, float] = {}
    features = (
        feature_index.query(((0.0, 180.0),), max_distance_km=max_distance_km)
        if feature_index is not None
        else snapshot.features
    )
    for feature in features:
        if feature.distance_km > max_distance_km:
            continue
        grouped.setdefault(column_key(feature), {})[feature.pressure_hpa] = feature
        identity = id(feature)
        position = feature_index._positions.get(identity) if feature_index is not None else None
        if position is not None:
            if feature.horizontal_cloud_probability is not None:
                horizontal_scores[identity] = clamp(
                    float(feature.horizontal_cloud_probability)
                )
                presence_scores[identity] = clamp(
                    float(feature.cloud_probability_given_cloud)
                    if feature.cloud_probability_given_cloud is not None
                    else horizontal_scores[identity]
                )
                explicit_scores[identity] = horizontal_scores[identity]
            else:
                presence_scores[identity] = feature_index._cloud_presence[position]
                explicit_scores[identity] = feature_index._explicit_presence[position]
            liquid_scores[identity] = feature_index._liquid_presence[position]
            ice_scores[identity] = feature_index._ice_presence[position]
        else:
            explicit = explicit_cloud_presence_score(feature)
            horizontal = feature.horizontal_cloud_probability
            conditional = feature.cloud_probability_given_cloud
            if horizontal is not None:
                horizontal_scores[identity] = clamp(float(horizontal))
                # Layer classification uses the conditional vertical profile;
                # horizontal occupancy is carried separately on the column.
                presence_scores[identity] = clamp(
                    float(conditional)
                    if conditional is not None
                    else float(horizontal)
                )
                explicit_scores[identity] = clamp(float(horizontal))
            else:
                presence_scores[identity] = (
                    cloud_presence_score(feature.rh_percent) if explicit is None else clamp(explicit)
                )
                explicit_scores[identity] = explicit or 0.0
            liquid_scores[identity] = condensate_component_score(feature.cloud_liquid_kg_kg)
            ice_scores[identity] = condensate_component_score(feature.cloud_ice_kg_kg)

    columns: ColumnIndex = {}
    for key, layers in grouped.items():
        reference = min(layers.values(), key=lambda item: item.distance_km)
        high_features = [layers[level] for level in (200, 250, 300, 400) if level in layers]
        mid_features = [layers[level] for level in (500, 600, 700) if level in layers]
        low_features = [layers[level] for level in (750, 800, 850, 925) if level in layers]
        high_presence = max((presence_scores[id(feature)] for feature in high_features), default=0.0)
        mid_presence = max((presence_scores[id(feature)] for feature in mid_features), default=0.0)
        low_presence = max((presence_scores[id(feature)] for feature in low_features), default=0.0)
        horizontal_cloud_probability = max(
            (horizontal_scores.get(id(feature), 0.0) for feature in layers.values()),
            default=0.0,
        )
        cloudy_layers = [feature for feature in layers.values() if presence_scores[id(feature)] >= 0.45]
        if not cloudy_layers and horizontal_cloud_probability >= 0.45:
            cloudy_layers = [
                feature
                for feature in layers.values()
                if presence_scores[id(feature)] > 0.0
            ]
        if cloudy_layers:
            base_m, top_m = estimated_column_cloud_bounds(layers, cloudy_layers, presence_scores=presence_scores)
            depth_m = max(0.0, top_m - base_m)
        else:
            base_m = 0.0
            top_m = 0.0
            depth_m = 0.0
        liquid_presence = max(
            (liquid_scores[id(feature)] for feature in layers.values()),
            default=0.0,
        )
        ice_presence = max(
            (ice_scores[id(feature)] for feature in layers.values()),
            default=0.0,
        )
        explicit_cloud_evidence = max(
            (
                max(explicit_scores[id(feature)], horizontal_scores.get(id(feature), 0.0))
                for feature in layers.values()
            ),
            default=0.0,
        )
        deep_cloud_risk = clamp(
            0.45 * min(high_presence, mid_presence)
            + 0.35 * min(mid_presence, low_presence)
            + 0.20 * clamp((len(cloudy_layers) - 2.0) / 4.0)
        )
        thickness_risk = clamp((depth_m - 2800.0) / 5200.0)
        cloud_type = classify_cloud_type(
            layers,
            high_presence,
            mid_presence,
            low_presence,
            deep_cloud_risk,
            thickness_risk,
            liquid_presence,
            ice_presence,
            explicit_cloud_evidence,
            presence_scores,
            horizontal_cloud_probability,
        )
        burnability = cloud_type_burnability_score(cloud_type, high_presence, mid_presence, low_presence, deep_cloud_risk, thickness_risk)
        columns[key] = CloudColumn(
            lat=reference.lat,
            lon=reference.lon,
            distance_km=reference.distance_km,
            bearing_deg=reference.bearing_deg,
            layers=layers,
            high_presence=high_presence,
            mid_presence=mid_presence,
            low_presence=low_presence,
            deep_cloud_risk=deep_cloud_risk,
            thickness_risk=thickness_risk,
            cloud_type=cloud_type,
            burnability_score=burnability,
            cloud_base_m=base_m,
            cloud_top_m=top_m,
            liquid_presence=liquid_presence,
            ice_presence=ice_presence,
            explicit_cloud_evidence=explicit_cloud_evidence,
            horizontal_cloud_probability=(
                horizontal_cloud_probability
                if any(id(feature) in horizontal_scores for feature in layers.values())
                else None
            ),
            footprint_azimuth_width_deg=reference.footprint_azimuth_width_deg,
            footprint_near_distance_km=reference.footprint_near_distance_km,
            footprint_far_distance_km=reference.footprint_far_distance_km,
        )
    return columns


def classify_cloud_type(
    layers: Dict[int, GridCellFeature],
    high_presence: float,
    mid_presence: float,
    low_presence: float,
    deep_cloud_risk: float,
    thickness_risk: float,
    liquid_presence: float = 0.0,
    ice_presence: float = 0.0,
    explicit_cloud_evidence: float = 0.0,
    presence_scores: Optional[Dict[int, float]] = None,
    horizontal_cloud_probability: Optional[float] = None,
) -> str:
    def conditional_support(pressure_levels: Sequence[int]) -> float:
        probabilities = []
        for pressure_hpa in pressure_levels:
            feature = layers.get(pressure_hpa)
            if feature is None:
                continue
            probability = (
                presence_scores[id(feature)]
                if presence_scores is not None
                else feature_cloud_presence_score(feature)
            )
            if probability > 0.0:
                probabilities.append(clamp(probability))
        support = 0.0
        for probability in probabilities:
            support = 1.0 - (1.0 - support) * (1.0 - probability)
        return clamp(support)

    conditional_profile = any(
        feature.cloud_probability_given_cloud is not None
        for feature in layers.values()
    )
    high_conditional_support = conditional_support((200, 250, 300, 400))
    mid_conditional_support = conditional_support((500, 600, 700))
    low_conditional_support = conditional_support((750, 800, 850, 925))
    if conditional_profile:
        # A split posterior may leave every individual level below 0.45.
        # Use the union within each height family to identify the dominant
        # layer; horizontal P only establishes that the footprint is cloudy.
        high_presence = max(high_presence, high_conditional_support)
        mid_presence = max(mid_presence, mid_conditional_support)
        low_presence = max(low_presence, low_conditional_support)
    strongest_high_level = min(
        (
            level
            for level in (200, 250, 300, 400)
            if level in layers
            and (
                presence_scores[id(layers[level])]
                if presence_scores is not None
                else feature_cloud_presence_score(layers[level])
            )
            >= (0.45 if not conditional_profile else 1e-9)
        ),
        default=None,
    )
    strongest_mid = max(mid_presence, 0.0)
    lift_values = [feature.omega_pa_s for feature in layers.values() if feature.omega_pa_s is not None]
    strongest_lift = min(lift_values, default=0.0)
    has_explicit_cloud = any(
        feature.horizontal_cloud_probability is not None
        or feature.cloud_probability_given_cloud is not None
        or feature.cloud_fraction_percent is not None
        or feature.cloud_liquid_kg_kg is not None
        or feature.cloud_ice_kg_kg is not None
        for feature in layers.values()
    )
    if deep_cloud_risk >= 0.68 and low_presence >= 0.45:
        return "深厚云盾"
    if deep_cloud_risk >= 0.58 and strongest_lift <= -0.25 and (
        not has_explicit_cloud or ice_presence >= 0.12
    ):
        return "积雨云云砧"
    if low_presence >= 0.62 and high_presence < 0.35 and mid_presence < 0.45:
        return "低云" if conditional_profile else "层云/层积云"
    if mid_presence >= 0.58 and high_presence < 0.45 and low_presence < 0.45:
        return "高积云"
    if high_presence >= 0.55 and strongest_mid >= 0.45:
        return "高层云"
    if high_presence >= 0.50 and strongest_high_level is not None and (
        not has_explicit_cloud
        or ice_presence >= max(0.12, liquid_presence * 0.65)
    ):
        return "卷云"
    if high_presence >= 0.50 and strongest_high_level is not None:
        return "高层云"
    if mid_presence >= 0.45:
        return "中云"
    if low_presence >= 0.45:
        return "低云"
    if conditional_profile and horizontal_cloud_probability is not None and horizontal_cloud_probability >= 0.45:
        family_support = {
            "high": high_conditional_support,
            "middle": mid_conditional_support,
            "low": low_conditional_support,
        }
        dominant_family = max(
            family_support,
            key=lambda family: (
                family_support[family],
                {
                    "high": high_presence,
                    "middle": mid_presence,
                    "low": low_presence,
                }[family],
                {"high": 0, "middle": 1, "low": 2}[family],
            ),
        )
        if family_support[dominant_family] > 0.0:
            return {
                "high": "高层云",
                "middle": "中云",
                "low": "低云",
            }[dominant_family]
    return "弱云/晴空"


_CLOUD_TYPE_SENSITIVITY = {
    "卷云": 0.70,
    "高积云": 0.60,
    "高层云": 0.50,
    "积雨云云砧": 0.40,
    "中云": 0.35,
    "层云/层积云": 0.35,
    "低云": 0.25,
    "深厚云盾": 0.35,
    "弱云/晴空": 0.35,
}


def cloud_type_burnability_score(
    cloud_type: str,
    high_presence: float,
    mid_presence: float,
    low_presence: float,
    deep_cloud_risk: float,
    thickness_risk: float,
) -> float:
    """Return a material burnability prior, not ideal-case scenic grandeur."""
    base = {
        "卷云": 0.93,
        "高积云": 0.90,
        "高层云": 0.80,
        "积雨云云砧": 0.76,
        "中云": 0.62,
        "层云/层积云": 0.34,
        "低云": 0.22,
        "深厚云盾": 0.28,
        "弱云/晴空": 0.08,
    }.get(cloud_type, 0.45)
    high_enough = clamp(max(high_presence, 0.75 * mid_presence))
    low_penalty = 0.28 * low_presence if cloud_type not in {"层云/层积云", "低云"} else 0.0
    thick_penalty = 0.34 * deep_cloud_risk + 0.18 * thickness_risk
    if cloud_type in {"卷云", "高积云"}:
        thick_penalty *= 0.55
    if cloud_type == "深厚云盾":
        thick_penalty *= 1.35
    # Dynamic optical thickness factor — neutral when no risk, penalize by risk × sensitivity
    sensitivity = _CLOUD_TYPE_SENSITIVITY.get(cloud_type, 0.35)
    thickness_optical = clamp(1.0 - sensitivity * (0.30 * thickness_risk + 0.20 * deep_cloud_risk))
    return clamp(base * thickness_optical * (0.55 + 0.45 * high_enough) - low_penalty - thick_penalty)


def column_for_feature(columns: Optional[ColumnIndex], feature: GridCellFeature) -> Optional[CloudColumn]:
    if columns is None:
        return None
    return columns.get(column_key(feature))


def lightpath_blocker_opacity(feature: GridCellFeature) -> float:
    opacity = feature_blocker_opacity_score(feature)
    if opacity <= 0.0:
        return 0.0
    if feature.pressure_hpa >= 700:
        layer_factor = 1.0
    elif feature.pressure_hpa >= 500:
        layer_factor = 0.82
    else:
        layer_factor = 0.58
    return clamp(opacity * layer_factor)


def observer_sightline_blocking_risk(
    snapshot: DomainSnapshot,
    target: GridCellFeature,
    target_height_m: float,
    feature_index: Optional[FeatureSpatialIndex] = None,
    observer_elevation_m: float = 0.0,
) -> float:
    penalties: List[float] = []
    for feature in indexed_features(
        snapshot,
        feature_index,
        ((target.bearing_deg, 14.0),),
        min_distance_km=15.0,
        max_distance_km=max(15.0, target.distance_km - 5.0),
        blockers_only=True,
    ):
        if feature is target:
            continue
        if feature.distance_km >= target.distance_km - 5.0:
            continue
        if feature.distance_km < 15.0:
            continue
        if angular_difference_deg(feature.bearing_deg, target.bearing_deg) > 14.0:
            continue
        opacity = feature_index.lightpath_opacity(feature) if feature_index is not None else lightpath_blocker_opacity(feature)
        if opacity <= 0.0:
            continue
        ray_height = sightline_height_at_distance_m(
            target.distance_km,
            target_height_m,
            feature.distance_km,
            observer_elevation_m,
        )
        cloud_base = feature_index.cloud_base(feature) if feature_index is not None else estimated_cloud_base_m(feature)
        intersection = vertical_ray_intersection_score(ray_height, cloud_base, feature.geopotential_height_m)
        if intersection <= 0.0:
            continue
        distance_factor = clamp(1.0 - feature.distance_km / max(target.distance_km, 1.0))
        penalties.append(opacity * intersection * (0.55 + 0.45 * distance_factor))
    return max(penalties, default=0.0)


def solar_inflow_blocking_risk(
    snapshot: DomainSnapshot,
    target: GridCellFeature,
    target_height_m: float,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    feature_index: Optional[FeatureSpatialIndex] = None,
    observer_elevation_m: float = 0.0,
) -> float:
    penalties: List[float] = []
    for feature in indexed_features(
        snapshot,
        feature_index,
        ((target.bearing_deg, 16.0), (solar_azimuth_deg, 18.0)),
        min_distance_km=target.distance_km + 8.0,
        max_distance_km=MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM,
        blockers_only=True,
    ):
        if feature is target:
            continue
        if feature.distance_km <= target.distance_km + 8.0:
            continue
        if feature.distance_km > MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM:
            continue
        gap_km = feature.distance_km - target.distance_km
        bearing_tolerance = 16.0 if gap_km <= 360.0 else 8.0
        solar_tolerance = 18.0 if gap_km <= 360.0 else 10.0
        if angular_difference_deg(feature.bearing_deg, target.bearing_deg) > bearing_tolerance and angular_difference_deg(feature.bearing_deg, solar_azimuth_deg) > solar_tolerance:
            continue
        opacity = feature_index.lightpath_opacity(feature) if feature_index is not None else lightpath_blocker_opacity(feature)
        if opacity <= 0.0:
            continue
        ray_height = solar_ray_height_at_distance_m(
            target.distance_km,
            target_height_m,
            feature.distance_km,
            solar_elevation_deg,
        )
        cloud_base = feature_index.cloud_base(feature) if feature_index is not None else estimated_cloud_base_m(feature)
        intersection = vertical_ray_intersection_score(ray_height, cloud_base, feature.geopotential_height_m, tolerance_m=850.0)
        if intersection <= 0.0:
            continue
        distance_factor = clamp(1.0 - (gap_km - 20.0) / (MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM - 20.0))
        alignment = 1.0 - max(
            angular_difference_deg(feature.bearing_deg, target.bearing_deg) / max(1.0, bearing_tolerance),
            angular_difference_deg(feature.bearing_deg, solar_azimuth_deg) / max(1.0, solar_tolerance),
        )
        far_factor = 1.0 if gap_km <= 360.0 else 0.72
        penalties.append(opacity * intersection * clamp(alignment) * (0.45 + 0.55 * distance_factor) * far_factor)
    return max(penalties, default=0.0)


def diagnose_solar_inflow_blockage(
    snapshot: DomainSnapshot,
    target: Optional[CandidateSignal],
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    feature_index: Optional[FeatureSpatialIndex] = None,
    observer_elevation_m: float = 0.0,
) -> Optional[SolarInflowBlockage]:
    if target is None:
        return None
    rows: List[tuple[float, GridCellFeature, float, float, float]] = []
    for feature in indexed_features(
        snapshot,
        feature_index,
        ((target.bearing_deg, 16.0), (solar_azimuth_deg, 18.0)),
        min_distance_km=target.distance_km + 8.0,
        max_distance_km=MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM,
        blockers_only=True,
    ):
        if abs(feature.distance_km - target.distance_km) < 8.0:
            continue
        if feature.distance_km <= target.distance_km + 8.0 or feature.distance_km > MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM:
            continue
        gap_km = feature.distance_km - target.distance_km
        bearing_tolerance = 16.0 if gap_km <= 360.0 else 8.0
        solar_tolerance = 18.0 if gap_km <= 360.0 else 10.0
        target_delta = angular_difference_deg(feature.bearing_deg, target.bearing_deg)
        solar_delta = angular_difference_deg(feature.bearing_deg, solar_azimuth_deg)
        if target_delta > bearing_tolerance and solar_delta > solar_tolerance:
            continue
        opacity = feature_index.lightpath_opacity(feature) if feature_index is not None else lightpath_blocker_opacity(feature)
        if opacity <= 0.0:
            continue
        ray_height = solar_ray_height_at_distance_m(
            target.distance_km,
            target.cloud_base_m,
            feature.distance_km,
            solar_elevation_deg,
        )
        cloud_base = feature_index.cloud_base(feature) if feature_index is not None else estimated_cloud_base_m(feature)
        intersection = vertical_ray_intersection_score(ray_height, cloud_base, feature.geopotential_height_m, tolerance_m=850.0)
        if intersection <= 0.0:
            continue
        distance_factor = clamp(1.0 - (gap_km - 20.0) / (MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM - 20.0))
        alignment = clamp(1.0 - max(target_delta / max(1.0, bearing_tolerance), solar_delta / max(1.0, solar_tolerance)))
        far_factor = 1.0 if gap_km <= 360.0 else 0.72
        score = opacity * intersection * alignment * (0.45 + 0.55 * distance_factor) * far_factor
        if score <= 0.0:
            continue
        rows.append((score, feature, ray_height, cloud_base, intersection))
    if not rows:
        return None
    rows.sort(key=lambda item: item[0], reverse=True)
    score, feature, ray_height, cloud_base, _ = rows[0]
    return SolarInflowBlockage(
        score=clamp(score),
        distance_km=feature.distance_km,
        bearing_deg=feature.bearing_deg,
        pressure_hpa=feature.pressure_hpa,
        rh_percent=feature.rh_percent,
        cloud_base_m=cloud_base,
        cloud_top_m=feature.geopotential_height_m,
        ray_height_m=ray_height,
        target_distance_km=target.distance_km,
        target_bearing_deg=target.bearing_deg,
        target_height_m=target.cloud_base_m,
    )


def path_clearance_weight(
    snapshot: DomainSnapshot,
    candidate: GridCellFeature,
    candidate_apparent_elevation_deg: float,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    feature_index: Optional[FeatureSpatialIndex] = None,
    observer_elevation_m: float = 0.0,
) -> float:
    # Target visibility is defined for the illuminated cloud base. Ignore any
    # caller-supplied top-height apparent angle so all scoring paths agree.
    # estimated_cloud_base_m(candidate) 对冻结候选是纯函数（只读气压层/位势高/
    # 地表温露点），下方 sightline/inflow 风险复用同一值：提升为局部量，
    # 避免每个候选重复计算 3 次。
    candidate_cloud_base_m = estimated_cloud_base_m(candidate)
    candidate_apparent_elevation_deg = apparent_cloud_elevation_deg(
        candidate.distance_km,
        candidate_cloud_base_m,
        observer_elevation_m,
    )
    penalties: List[float] = []
    for feature in indexed_features(
        snapshot,
        feature_index,
        ((candidate.bearing_deg, 12.0),),
        min_distance_km=20.0,
        max_distance_km=candidate.distance_km + 220.0,
        blockers_only=True,
    ):
        if feature is candidate:
            continue
        if feature.pressure_hpa <= candidate.pressure_hpa:
            continue
        if feature.distance_km < 20.0:
            continue
        if angular_difference_deg(feature.bearing_deg, candidate.bearing_deg) > 12.0:
            continue
        opacity = feature_index.blocker_opacity(feature) if feature_index is not None else feature_blocker_opacity_score(feature)
        if opacity <= 0.0:
            continue
        apparent_elevation = (
            feature_index.apparent_top(feature)
            if feature_index is not None
            else apparent_cloud_elevation_deg(
                feature.distance_km,
                feature.geopotential_height_m,
                observer_elevation_m,
            )
        )
        if feature.distance_km < candidate.distance_km:
            if apparent_elevation >= candidate_apparent_elevation_deg - 0.25:
                distance_factor = clamp(1.0 - feature.distance_km / max(candidate.distance_km, 1.0))
                penalties.append(opacity * (0.55 + 0.45 * distance_factor))
        elif feature.distance_km <= candidate.distance_km + 220.0 and feature.geopotential_height_m <= candidate.geopotential_height_m + 1200.0:
            distance_factor = clamp(1.0 - (feature.distance_km - candidate.distance_km) / 220.0)
            layer_factor = 1.0 if feature.pressure_hpa >= candidate.pressure_hpa else 0.55
            penalties.append(0.65 * opacity * distance_factor * layer_factor)
    legacy_risk = max(penalties, default=0.0)
    sightline_risk = observer_sightline_blocking_risk(
        snapshot,
        candidate,
        candidate_cloud_base_m,
        feature_index,
        observer_elevation_m,
    )
    inflow_risk = solar_inflow_blocking_risk(
        snapshot,
        candidate,
        candidate_cloud_base_m,
        solar_elevation_deg,
        solar_azimuth_deg,
        feature_index,
        observer_elevation_m,
    )
    return clamp(1.0 - max(legacy_risk, sightline_risk, inflow_risk))


def boundary_distance_weight(
    layer: str,
    distance_km: float,
    cloud_base_m: Optional[float] = None,
    observer_elevation_m: float = 0.0,
) -> float:
    representative_base = {"low": 1800.0, "mid": 4500.0, "high": 8000.0}
    return cloud_edge_distance_quality(
        distance_km,
        cloud_base_m or representative_base.get(layer, 4500.0),
        observer_elevation_m,
    )


def boundary_path_clearance(
    snapshot: DomainSnapshot,
    boundary: GridCellFeature,
    cloud_base_m: float,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    feature_index: Optional[FeatureSpatialIndex] = None,
    observer_elevation_m: float = 0.0,
) -> float:
    penalties: List[float] = []
    target_elevation = apparent_cloud_elevation_deg(
        boundary.distance_km,
        cloud_base_m,
        observer_elevation_m,
    )
    for feature in indexed_features(
        snapshot,
        feature_index,
        ((boundary.bearing_deg, 16.0),),
        min_distance_km=15.0,
        max_distance_km=max(15.0, boundary.distance_km - 5.0),
        blockers_only=True,
    ):
        if feature is boundary:
            continue
        if feature.distance_km >= boundary.distance_km - 5.0:
            continue
        if feature.distance_km < 15.0:
            continue
        if angular_difference_deg(feature.bearing_deg, boundary.bearing_deg) > 16.0:
            continue
        opacity = feature_index.blocker_opacity(feature) if feature_index is not None else feature_blocker_opacity_score(feature)
        if opacity <= 0.0:
            continue
        blocker_base = feature_index.cloud_base(feature) if feature_index is not None else estimated_cloud_base_m(feature)
        blocker_top = feature.geopotential_height_m
        blocker_elevation = (
            feature_index.apparent_top(feature)
            if feature_index is not None
            else apparent_cloud_elevation_deg(
                feature.distance_km,
                blocker_top,
                observer_elevation_m,
            )
        )
        if blocker_elevation < target_elevation - 0.35 and blocker_base > cloud_base_m + 800.0:
            continue
        distance_factor = clamp(1.0 - feature.distance_km / max(boundary.distance_km, 1.0))
        layer_factor = 1.0 if feature.pressure_hpa >= boundary.pressure_hpa else 0.55
        penalties.append(opacity * layer_factor * (0.55 + 0.45 * distance_factor))
    legacy_risk = max(penalties, default=0.0)
    sightline_risk = observer_sightline_blocking_risk(
        snapshot,
        boundary,
        cloud_base_m,
        feature_index,
        observer_elevation_m,
    )
    inflow_risk = solar_inflow_blocking_risk(
        snapshot,
        boundary,
        cloud_base_m,
        solar_elevation_deg,
        solar_azimuth_deg,
        feature_index,
        observer_elevation_m,
    )
    return clamp(1.0 - max(legacy_risk, sightline_risk, inflow_risk))


def boundary_edge_contrast_score(
    snapshot: DomainSnapshot,
    boundary: GridCellFeature,
    feature_index: Optional[FeatureSpatialIndex] = None,
) -> float:
    boundary_opacity = feature_index.cloud_opacity(boundary) if feature_index is not None else feature_cloud_opacity_score(boundary)
    if boundary_opacity <= 0.0:
        return 0.0

    comparison_opacities: List[float] = []
    if feature_index is None:
        comparison_features: Sequence[GridCellFeature] = snapshot.features
    else:
        comparison_features = [
            *feature_index.query_pressure(
                ((boundary.bearing_deg, 28.0),),
                boundary.pressure_hpa,
                min_distance_km=max(0.0, boundary.distance_km - 95.0),
                max_distance_km=boundary.distance_km + 95.0,
            ),
            *feature_index.query_pressure(
                ((boundary.bearing_deg, 16.0),),
                boundary.pressure_hpa,
                min_distance_km=20.0,
                max_distance_km=max(20.0, boundary.distance_km - 12.0),
            ),
        ]
    seen: set[int] = set()
    for feature in comparison_features:
        identity = id(feature)
        if identity in seen:
            continue
        if feature is boundary:
            continue
        if feature.pressure_hpa != boundary.pressure_hpa:
            continue
        sector_delta = angular_difference_deg(feature.bearing_deg, boundary.bearing_deg)
        distance_delta = abs(feature.distance_km - boundary.distance_km)
        if sector_delta <= 28.0 and distance_delta <= 95.0:
            comparison_opacities.append(feature_index.cloud_opacity(feature) if feature_index is not None else feature_cloud_opacity_score(feature))
            seen.add(identity)
            continue
        if sector_delta <= 16.0 and 20.0 <= feature.distance_km < boundary.distance_km - 12.0:
            comparison_opacities.append(feature_index.cloud_opacity(feature) if feature_index is not None else feature_cloud_opacity_score(feature))
            seen.add(identity)

    if len(comparison_opacities) < 3:
        return 0.72
    clearest_neighbor = min(comparison_opacities)
    return clamp((boundary_opacity - clearest_neighbor) / 0.72)


def boundary_clear_slot_score(
    snapshot: DomainSnapshot,
    boundary: GridCellFeature,
    solar_azimuth_deg: float,
    feature_index: Optional[FeatureSpatialIndex] = None,
) -> float:
    penalties: List[float] = []
    for feature in indexed_features(
        snapshot,
        feature_index,
        ((boundary.bearing_deg, 18.0), (solar_azimuth_deg, 22.0)),
        min_distance_km=15.0,
        max_distance_km=max(15.0, boundary.distance_km - 8.0),
        blockers_only=True,
    ):
        if feature is boundary:
            continue
        if feature.pressure_hpa < boundary.pressure_hpa:
            continue
        if feature.distance_km < 15.0 or feature.distance_km >= boundary.distance_km - 8.0:
            continue
        if angular_difference_deg(feature.bearing_deg, boundary.bearing_deg) > 18.0 and angular_difference_deg(feature.bearing_deg, solar_azimuth_deg) > 22.0:
            continue
        opacity = feature_index.blocker_opacity(feature) if feature_index is not None else feature_blocker_opacity_score(feature)
        if opacity <= 0.0:
            continue
        distance_factor = clamp(1.0 - feature.distance_km / max(boundary.distance_km, 1.0))
        lower_layer_factor = 1.0 if feature.pressure_hpa >= 700 else 0.72
        penalties.append(opacity * lower_layer_factor * (0.55 + 0.45 * distance_factor))
    if not penalties:
        return 1.0
    return clamp(1.0 - max(penalties))


def boundary_thickness_score(snapshot: DomainSnapshot, boundary: GridCellFeature, columns: Optional[ColumnIndex] = None) -> float:
    column = column_for_feature(columns, boundary)
    if column is not None:
        return clamp(1.0 - 0.55 * column.thickness_risk - 0.35 * column.deep_cloud_risk)

    cloudy_layers: set[int] = set()
    for feature in snapshot.features:
        if feature.pressure_hpa > 700:
            continue
        if angular_difference_deg(feature.bearing_deg, boundary.bearing_deg) > 16.0:
            continue
        if abs(feature.distance_km - boundary.distance_km) > 40.0:
            continue
        if feature_cloud_opacity_score(feature) > 0.35:
            cloudy_layers.add(feature.pressure_hpa)

    layer_count = len(cloudy_layers)
    if layer_count <= 2:
        return 1.0
    if layer_count == 3:
        return 0.85
    if layer_count == 4:
        return 0.65
    return 0.45


def column_layer_presence(column: CloudColumn, layer: str) -> float:
    return {
        "low": column.low_presence,
        "mid": column.mid_presence,
        "high": column.high_presence,
    }[layer]


def column_layer_features(column: CloudColumn, layer: str) -> List[GridCellFeature]:
    return [
        feature
        for feature in column.layers.values()
        if layer_name(feature.pressure_hpa) == layer
    ]


def local_deck_cloud_base_m(column: CloudColumn, layer: str) -> float:
    features = column_layer_features(column, layer)
    if not features:
        return 0.0
    return min(estimated_cloud_base_m(feature) for feature in features)


def physical_transmission_quality(transmission: float) -> float:
    """Map direct 550 nm transmission to the empirical visibility bands in the tutorial."""
    value = clamp(transmission, 0.0, 1.0)
    invisible = 1.0 / 5000.0
    weak = 1.0 / 1000.0
    bright = 1.0 / 50.0
    if value <= invisible:
        return 0.0
    if value <= weak:
        return 0.18 * math.log(value / invisible) / math.log(weak / invisible)
    if value <= bright:
        return 0.18 + 0.52 * math.log(value / weak) / math.log(bright / weak)
    return clamp(0.70 + 0.30 * math.log(value / bright) / math.log(1.0 / bright))


def soft_knee(x: float, threshold: float = 0.18, knee_width: float = 0.22) -> float:
    """Soft-knee 过渡：让 low physical_existence 时评分渐近趋近而非截断。"""
    x = clamp(x)
    low = threshold - knee_width * 0.5
    high = threshold + knee_width * 0.5
    if x <= low:
        return 0.30 * x
    if x >= high:
        return x
    t = (x - low) / knee_width
    return 0.30 * low + (high - 0.30 * low) * (t * t * (3.0 - 2.0 * t))


def combine_regime_burn_score(
    physical_existence_score: float,
    geometric_extent: float,
    vividness: float,
    solar_weight: float,
    visibility_gate_score: float,
    profile_factor: float,
    duration_factor: float = 1.0,
    path_illumination_factor: float = 1.0,
) -> float:
    """Deterministic burn verdict with one target-specific optical correction."""
    necessary_ceiling = min(
        soft_knee(physical_existence_score),
        solar_weight,
        visibility_gate_score,
        profile_factor,
    )
    quality_fill = clamp(0.72 + 0.18 * vividness + 0.10 * geometric_extent)
    return clamp(
        necessary_ceiling
        * quality_fill
        * clamp(duration_factor, low=0.50, high=1.12)
        * clamp(path_illumination_factor, low=0.70, high=1.0)
    )


def combine_regime_intensity_score(
    physical_existence_score: float,
    geometric_extent: float,
    vividness: float,
    solar_weight: float,
    visibility_gate_score: float,
    profile_factor: float,
    path_illumination_factor: float = 1.0,
) -> float:
    """Deterministic intensity: weakest necessary gate caps it, quality fills it in."""
    necessary_ceiling = min(
        soft_knee(physical_existence_score),
        solar_weight,
        visibility_gate_score,
        profile_factor,
    )
    quality_fill = clamp(0.56 + 0.36 * vividness + 0.08 * geometric_extent)
    return clamp(
        necessary_ceiling
        * quality_fill
        * clamp(path_illumination_factor, low=0.70, high=1.0)
    )


def local_deck_boundary_uncertainty(
    boundary: BoundarySignal,
    solar_elevation_deg: float,
    observer_latitude_deg: float,
    visible_extent: float,
    vividness: float,
    solar_weight: float,
    visibility_gate_score: float,
    profile_factor: float,
    nominal_burn_score: float,
    path_illumination_factor: float = 1.0,
    event_threshold: float = BURNABLE_CELL_SCORE,
    observer_elevation_m: float = 0.0,
) -> Optional[BoundaryUncertainty]:
    """Propagate the observed cloud/clear bracket through the local-deck score."""
    lower = boundary.edge_distance_lower_km
    upper = boundary.edge_distance_upper_km
    if lower is None or upper is None:
        return None
    lower = max(1.0, min(float(lower), float(upper)))
    upper = max(lower, max(float(lower), float(upper)))
    if upper - lower < 0.1:
        return None
    nominal = min(max(float(boundary.distance_km), lower), upper)
    nominal_geometry = cloud_edge_geometry_score(
        nominal,
        boundary.cloud_base_m,
        solar_elevation_deg,
        observer_elevation_m,
    )
    if nominal_geometry <= 0.0:
        return None

    def score_at(distance_km: float) -> float:
        geometry = cloud_edge_geometry_score(
            distance_km,
            boundary.cloud_base_m,
            solar_elevation_deg,
            observer_elevation_m,
        )
        physical_existence = clamp(
            boundary.score * geometry / nominal_geometry
        )
        duration = estimate_legacy_glow_duration_minutes(
            boundary.geometric_reach_km,
            distance_km,
            observer_latitude_deg,
        )
        return combine_regime_burn_score(
            physical_existence,
            visible_extent,
            vividness,
            solar_weight,
            visibility_gate_score,
            profile_factor,
            duration_quality_factor(duration),
            path_illumination_factor,
        )

    bracket_distances = np.linspace(lower, upper, 41)
    bracket_scores = [score_at(float(distance)) for distance in bracket_distances]
    nominal_event = nominal_burn_score >= event_threshold
    classification_stable = all(
        (score >= event_threshold) == nominal_event for score in bracket_scores
    )

    maximum_distance = cloud_edge_max_distance_km(
        boundary.cloud_base_m,
        observer_elevation_m,
    )
    search_distances = np.linspace(1.0, max(1.0, maximum_distance), 801)
    flip_displacements = [
        abs(float(distance) - nominal)
        for distance in search_distances
        if (score_at(float(distance)) >= event_threshold) != nominal_event
    ]
    return BoundaryUncertainty(
        regime="local_deck",
        source="model-cloud-clear-bracket",
        nominal_distance_km=nominal,
        lower_distance_km=lower,
        upper_distance_km=upper,
        width_km=upper - lower,
        score_min=min(bracket_scores),
        score_max=max(bracket_scores),
        event_threshold=event_threshold,
        classification_stable=classification_stable,
        nearest_flip_displacement_km=min(flip_displacements, default=None),
    )


def estimate_legacy_glow_duration_minutes(
    geometric_reach_km: float,
    edge_distance_km: float,
    latitude_deg: float,
) -> float:
    """Estimate duration for the legacy scene scorer only.

    The authoritative point/regional quality path measures duration from its
    active time samples. This latitude-only approximation remains isolated so
    it cannot be mistaken for the geometric window or reused by new scoring.
    """
    v = LEGACY_LATITUDE_LINE_SPEED_KM_PER_MIN * math.cos(math.radians(abs(latitude_deg)))
    if v <= 0.0 or geometric_reach_km <= 0.0:
        return 0.0
    effective_reach = min(geometric_reach_km, edge_distance_km) if edge_distance_km > 0.0 else geometric_reach_km
    return 2.0 * effective_reach / v


def local_deck_aerosol_transmission_samples(
    aod550: float,
    inflow_aod550: float,
    cloud_base_m: float,
    solar_elevation_deg: float,
    observer_elevation_m: float = 0.0,
) -> List[float]:
    # NOTE: Do NOT apply a separate optical-ground correction here — the path integration
    # below (inflow_tau + view_tau) already accounts for the full AOD column.
    # Applying optical ground would double-count aerosol extinction.
    samples: List[float] = []
    for scale_height_m in AEROSOL_SCALE_HEIGHTS_M:
        inflow_tau = solar_inflow_aerosol_optical_depth(
            inflow_aod550,
            0.0,
            cloud_base_m,
            solar_elevation_deg,
            scale_height_m=scale_height_m,
            observer_elevation_m=observer_elevation_m,
        )
        view_tau = max(0.0, aod550) * (
            1.0
            - math.exp(
                -max(0.0, cloud_base_m - observer_elevation_m)
                / max(1.0, scale_height_m)
            )
        )
        samples.append(math.exp(-min(20.0, inflow_tau + view_tau)))
    return samples


def local_deck_path_clearance(
    columns: Sequence[CloudColumn],
    layer: str,
    cloud_base_m: float,
    edge_distance_km: float,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    observer_elevation_m: float = 0.0,
) -> float:
    risks: List[float] = []
    for column in columns:
        if column.distance_km < 8.0 or column.distance_km > MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM:
            continue
        if angular_difference_deg(column.bearing_deg, solar_azimuth_deg) > 18.0:
            continue
        ray_height_m = solar_ray_height_at_distance_m(
            0.0,
            cloud_base_m,
            column.distance_km,
            solar_elevation_deg,
        )
        if ray_height_m <= 0.0:
            return 0.0
        for feature in column.layers.values():
            feature_layer = layer_name(feature.pressure_hpa)
            blocker_base_m = estimated_cloud_base_m(feature)
            # Before the same-layer edge, the post-sunset ray travels below the target deck.
            if (
                feature_layer == layer
                and column.distance_km < edge_distance_km
                and ray_height_m < blocker_base_m
            ):
                continue
            opacity = feature_blocker_opacity_score(feature)
            if opacity <= 0.0:
                continue
            intersection = vertical_ray_intersection_score(
                ray_height_m,
                blocker_base_m,
                feature.geopotential_height_m,
                tolerance_m=220.0,
            )
            if intersection > 0.0:
                risks.append(opacity * intersection)
    return clamp(1.0 - max(risks, default=0.0))


def local_deck_edge_signals(
    snapshot: DomainSnapshot,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    aerosol: AerosolReading,
    columns: Optional[ColumnIndex] = None,
    observer_elevation_m: float = 0.0,
) -> List[BoundarySignal]:
    """Diagnose the tutorial's canonical case: local cloud deck with a sunward clear edge."""
    column_values = list((columns if columns is not None else build_cloud_columns(snapshot)).values())
    if not column_values:
        return []

    current_reach_km = cloud_edge_reach_km(solar_elevation_deg)
    # The deck base is an MSL geopotential height, so the shadow threshold
    # must be MSL too; anchoring it to the observer shifted it by a full
    # station altitude and misjudged elevated decks.
    required_base_m = required_illumination_height_m(
        0.0,
        solar_elevation_deg,
    )
    signals: List[BoundarySignal] = []

    for layer in ("low", "mid", "high"):
        local_rows = [
            column
            for column in column_values
            if column.distance_km <= 70.0 and column_layer_presence(column, layer) > 0.0
        ]
        if not local_rows:
            continue
        local_rows.sort(key=lambda column: column.distance_km)
        weighted_presence = []
        for column in local_rows:
            weight = math.exp(-column.distance_km / 45.0)
            weighted_presence.append((column_layer_presence(column, layer), weight, column))
        local_mean = sum(value * weight for value, weight, _ in weighted_presence) / max(
            1e-9, sum(weight for _, weight, _ in weighted_presence)
        )
        local_peak = max(value for value, _, _ in weighted_presence)
        local_presence = clamp(0.65 * local_peak + 0.35 * local_mean)
        if local_presence < 0.42:
            continue
        target_column = max(
            local_rows,
            key=lambda column: column_layer_presence(column, layer)
            * math.exp(-column.distance_km / 80.0),
        )
        cloud_base_m = local_deck_cloud_base_m(target_column, layer)
        if cloud_base_m <= required_base_m:
            continue
        maximum_edge_distance_km = cloud_edge_max_distance_km(
            cloud_base_m,
            observer_elevation_m,
        )
        geometric_reach_km = current_reach_km or maximum_edge_distance_km

        corridor = sorted(
            (
                column
                for column in column_values
                if 20.0 <= column.distance_km <= MAX_CANDIDATE_DISTANCE_KM
                and angular_difference_deg(column.bearing_deg, solar_azimuth_deg) <= 22.0
            ),
            key=lambda column: column.distance_km,
        )
        last_cloud_distance = 0.0
        edge_column: Optional[CloudColumn] = None
        clear_rows: List[CloudColumn] = []
        for index, column in enumerate(corridor):
            presence = column_layer_presence(column, layer)
            if presence >= 0.45:
                last_cloud_distance = column.distance_km
                continue
            if presence > 0.30 or column.distance_km < max(30.0, last_cloud_distance + 8.0):
                continue
            following = [
                row
                for row in corridor[index : index + 6]
                if row.distance_km <= column.distance_km + 150.0
            ]
            if not following:
                continue
            if mean(column_layer_presence(row, layer) for row in following) > 0.40:
                continue
            edge_column = column
            clear_rows = following
            break
        if edge_column is None:
            continue

        edge_distance_km = 0.5 * (last_cloud_distance + edge_column.distance_km)
        geometry_score = cloud_edge_geometry_score(
            edge_distance_km,
            cloud_base_m,
            solar_elevation_deg,
            observer_elevation_m,
        )
        if geometry_score <= 0.0:
            continue
        clear_presence = mean(column_layer_presence(row, layer) for row in clear_rows)
        edge_contrast = clamp((local_presence - clear_presence) / 0.65)
        clear_slot = clamp((0.50 - clear_presence) / 0.50)
        if edge_contrast < 0.30 or clear_slot < 0.35:
            continue

        clearance = local_deck_path_clearance(
            column_values,
            layer,
            cloud_base_m,
            edge_distance_km,
            solar_elevation_deg,
            solar_azimuth_deg,
            observer_elevation_m,
        )
        if clearance <= 0.10:
            continue
        _, inflow_path_aod, path_variability = path_aod_components(
            aerosol,
            target_column.lat,
            target_column.lon,
            0.0,
            solar_azimuth_deg,
            solar_azimuth_deg,
        )
        transmission_samples = local_deck_aerosol_transmission_samples(
            aerosol.aod550,
            inflow_path_aod,
            cloud_base_m,
            solar_elevation_deg,
            observer_elevation_m,
        )
        transmission_qualities = [physical_transmission_quality(value) for value in transmission_samples]
        transmission_spread = max(transmission_qualities) - min(transmission_qualities)
        aerosol_quality = clamp(mean(transmission_qualities) - 0.25 * transmission_spread)
        thickness = clamp(1.0 - 0.55 * target_column.deep_cloud_risk - 0.25 * target_column.thickness_risk)
        score = clamp(
            local_presence
            * geometry_score
            * clearance
            * (0.58 + 0.42 * edge_contrast)
            * (0.72 + 0.28 * clear_slot)
            * thickness
        )
        if score <= 0.12:
            continue

        layer_features = column_layer_features(target_column, layer)
        cloud_top_m = max((feature.geopotential_height_m for feature in layer_features), default=cloud_base_m)
        signals.append(
            BoundarySignal(
                layer=layer,
                distance_km=edge_distance_km,
                bearing_deg=solar_azimuth_deg,
                sector_delta_deg=0.0,
                cloud_base_m=cloud_base_m,
                cloud_top_m=cloud_top_m,
                required_base_m=required_base_m,
                apparent_base_elevation_deg=apparent_cloud_elevation_deg(
                    edge_distance_km,
                    cloud_base_m,
                    observer_elevation_m,
                ),
                path_clearance_score=clearance,
                aerosol_transmission_score=aerosol_quality,
                score=score,
                edge_contrast_score=edge_contrast,
                clear_slot_score=clear_slot,
                thickness_score=thickness,
                cloud_type=target_column.cloud_type,
                cloud_type_burnability_score=target_column.burnability_score,
                deep_cloud_risk=target_column.deep_cloud_risk,
                aerosol_sensitivity_score=transmission_spread,
                aerosol_path_variability_score=path_variability,
                observer_path_aod550=aerosol.aod550,
                inflow_path_aod550=inflow_path_aod,
                regime="local_deck",
                local_cloud_presence_score=local_presence,
                geometric_reach_km=geometric_reach_km,
                physical_transmission_min=min(transmission_samples),
                physical_transmission_max=max(transmission_samples),
                atmospheric_clarity_score=atmospheric_clarity_score(aerosol.aod550),
                edge_distance_lower_km=last_cloud_distance,
                edge_distance_upper_km=edge_column.distance_km,
            )
        )

    signals.sort(key=lambda item: item.score, reverse=True)
    return signals


def boundary_lightpath_signals(
    snapshot: DomainSnapshot,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    aerosol: AerosolReading,
    columns: Optional[ColumnIndex] = None,
    feature_index: Optional[FeatureSpatialIndex] = None,
    observer_elevation_m: float = 0.0,
) -> List[BoundarySignal]:
    signals: List[BoundarySignal] = []
    boundary_rows = []
    native_boundary_rows = (
        feature_index.boundary_rows(solar_azimuth_deg)
        if feature_index is not None
        else None
    )
    if native_boundary_rows is None:
        source_rows = (
            (feature, None, None)
            for feature in indexed_features(
                snapshot,
                feature_index,
                ((solar_azimuth_deg, 55.0),),
                min_distance_km=40.0,
                max_distance_km=360.0,
            )
        )
    else:
        source_rows = iter(native_boundary_rows)
    for feature, native_sector_delta, native_opacity in source_rows:
        layer = layer_name(feature.pressure_hpa)
        if layer == "low":
            continue
        if feature.distance_km < 40.0 or feature.distance_km > 360.0:
            continue
        sector_delta = (
            float(native_sector_delta)
            if native_sector_delta is not None
            else angular_difference_deg(feature.bearing_deg, solar_azimuth_deg)
        )
        if sector_delta > 55.0:
            continue
        opacity = (
            float(native_opacity)
            if native_opacity is not None
            else feature_index.cloud_opacity(feature) if feature_index is not None else feature_cloud_opacity_score(feature)
        )
        if opacity <= 0.0:
            continue

        column = column_for_feature(columns, feature)
        cloud_base = (
            column.cloud_base_m
            if column is not None and column.cloud_base_m > 0.0
            else estimated_cloud_base_m(feature)
        )
        required_base = required_illumination_height_m(
            feature.distance_km,
            solar_elevation_deg,
            sector_delta,
        )
        illumination = illumination_feasibility(cloud_base, required_base)
        if illumination <= 0.0:
            continue

        apparent_base = apparent_cloud_elevation_deg(
            feature.distance_km,
            cloud_base,
            observer_elevation_m,
        )
        sight = line_of_sight_weight(apparent_base)
        if sight <= 0.0:
            continue

        boundary_rows.append(
            (
                feature,
                layer,
                sector_delta,
                opacity,
                column,
                cloud_base,
                required_base,
                illumination,
                apparent_base,
                sight,
            )
        )

    native_clearances = (
        feature_index.boundary_clearance_weights(
            [row[0] for row in boundary_rows],
            [row[5] for row in boundary_rows],
            solar_elevation_deg,
            solar_azimuth_deg,
        )
        if feature_index is not None
        else None
    )
    native_contexts = (
        feature_index.boundary_context_scores(
            [row[0] for row in boundary_rows],
            solar_azimuth_deg,
        )
        if feature_index is not None
        else None
    )
    eligible_boundaries = []
    aerosol_inputs = []
    for row_index, row in enumerate(boundary_rows):
        feature, layer, sector_delta, opacity, column, cloud_base, required_base, illumination, apparent_base, sight = row
        clearance = (
            native_clearances[row_index]
            if native_clearances is not None
            else boundary_path_clearance(
                snapshot,
                feature,
                cloud_base,
                solar_elevation_deg,
                solar_azimuth_deg,
                feature_index,
                observer_elevation_m,
            )
        )
        if clearance <= 0.05:
            continue
        observer_path_aod, inflow_path_aod, path_variability = path_aod_components(
            aerosol,
            feature.lat,
            feature.lon,
            feature.distance_km,
            feature.bearing_deg,
            solar_azimuth_deg,
        )
        eligible_boundaries.append(
            (
                row_index,
                row,
                clearance,
                observer_path_aod,
                inflow_path_aod,
                path_variability,
            )
        )
        aerosol_inputs.append(
            (
                aerosol.aod550,
                observer_path_aod,
                inflow_path_aod,
                feature.distance_km,
                cloud_base,
                apparent_base,
                solar_elevation_deg,
                observer_elevation_m,
            )
        )

    try:
        aerosol_metrics = native_aerosol_transmission_batch(
            np.asarray(aerosol_inputs, dtype=np.float64).reshape((-1, 8))
        )
    except Exception:
        aerosol_metrics = np.empty((len(aerosol_inputs), 5), dtype=np.float64)
        for index, values in enumerate(aerosol_inputs):
            samples = aerosol_lightpath_transmission_samples(
                values[0],
                values[3],
                values[4],
                values[5],
                values[6],
                observer_aod550=values[1],
                inflow_aod550=values[2],
                observer_elevation_m=values[7],
            )
            aerosol_metrics[index, :3] = samples
            aerosol_metrics[index, 3] = aerosol_transmission_weight_from_samples(samples)
            aerosol_metrics[index, 4] = clamp(max(samples) - min(samples))

    for eligible_index, eligible in enumerate(eligible_boundaries):
        row_index, row, clearance, observer_path_aod, inflow_path_aod, path_variability = eligible
        feature, layer, sector_delta, opacity, column, cloud_base, required_base, illumination, apparent_base, sight = row
        aerosol_weight = float(aerosol_metrics[eligible_index, 3])
        aerosol_sensitivity = float(aerosol_metrics[eligible_index, 4])
        if native_contexts is not None:
            edge_contrast, clear_slot = native_contexts[row_index]
        else:
            edge_contrast = boundary_edge_contrast_score(snapshot, feature, feature_index)
            clear_slot = boundary_clear_slot_score(snapshot, feature, solar_azimuth_deg, feature_index)
        type_burnability = column.burnability_score if column is not None else 0.5
        cloud_type = column.cloud_type if column is not None else "unknown"
        deep_risk = column.deep_cloud_risk if column is not None else 0.0
        thickness = boundary_thickness_score(snapshot, feature, columns)
        azimuth_score = clamp(1.0 - sector_delta / 55.0)
        distance_score = cloud_edge_geometry_score(
            feature.distance_km,
            cloud_base,
            solar_elevation_deg,
            observer_elevation_m,
        )
        if distance_score <= 0.0:
            continue
        layer_weight = 1.0 if layer == "mid" else 0.82
        base_score = clamp(
            (
                0.24 * opacity
                + 0.18 * illumination
                + 0.14 * clearance
                + 0.14 * edge_contrast
                + 0.10 * clear_slot
                + 0.08 * distance_score
                + 0.06 * azimuth_score
                + 0.04 * sight
                + 0.02 * thickness
            )
            * layer_weight
        )
        type_factor = 0.72 + 0.28 * type_burnability
        score = clamp(base_score * (0.55 + 0.45 * edge_contrast) * (0.75 + 0.25 * clear_slot) * thickness * type_factor)
        if score <= 0.18:
            continue
        signals.append(
            BoundarySignal(
                layer=layer,
                distance_km=feature.distance_km,
                bearing_deg=feature.bearing_deg,
                sector_delta_deg=sector_delta,
                cloud_base_m=cloud_base,
                cloud_top_m=feature.geopotential_height_m,
                required_base_m=required_base,
                apparent_base_elevation_deg=apparent_base,
                path_clearance_score=clearance,
                aerosol_transmission_score=aerosol_weight,
                score=score,
                edge_contrast_score=edge_contrast,
                clear_slot_score=clear_slot,
                thickness_score=thickness,
                cloud_type=cloud_type,
                cloud_type_burnability_score=type_burnability,
                deep_cloud_risk=deep_risk,
                aerosol_sensitivity_score=aerosol_sensitivity,
                aerosol_path_variability_score=path_variability,
                observer_path_aod550=observer_path_aod,
                inflow_path_aod550=inflow_path_aod,
                atmospheric_clarity_score=atmospheric_clarity_score(aerosol.aod550),
            )
        )
    signals.sort(key=lambda item: item.score, reverse=True)
    return signals


def column_path_presence(column: CloudColumn) -> float:
    return clamp(max(column.low_presence, 0.86 * column.mid_presence, 0.65 * column.high_presence))


def _cloud_hole_min_penetration_km(
    hole_distance_km: float,
    solar_elevation_deg: float,
    sector_delta_deg: float,
    cloud_thickness_m: float,
) -> float:
    """Minimum horizontal penetration through a cloud hole (km).

    Mirrors native cloud_hole_min_penetration_km: computes how far sunlight must
    travel horizontally through the hole gap at the cloud layer height.
    Returns infinity when the geometry makes illumination impossible.
    """
    directional_distance = hole_distance_km * math.cos(math.radians(sector_delta_deg))
    local_elevation_rad = math.radians(
        solar_illumination_elevation_deg(solar_elevation_deg)
    ) + directional_distance / EARTH_RADIUS_KM
    if local_elevation_rad >= 0.0:
        return 0.0
    abs_elev = min(-local_elevation_rad, math.radians(20.0))
    if abs_elev < 1e-6:
        return float("inf")
    return cloud_thickness_m / (math.tan(abs_elev) * 1000.0)


def cloud_hole_clear_slot_score(column: CloudColumn) -> float:
    return clamp((0.56 - column_path_presence(column)) / 0.56)


def cloud_hole_edge_contrast_score(columns: Sequence[CloudColumn], hole: CloudColumn) -> float:
    hole_presence = column_path_presence(hole)
    neighbor_presence: List[float] = []
    for column in columns:
        if column is hole:
            continue
        az_delta = angular_difference_deg(column.bearing_deg, hole.bearing_deg)
        distance_delta = abs(column.distance_km - hole.distance_km)
        if az_delta <= 34.0 and 20.0 <= distance_delta <= 95.0:
            neighbor_presence.append(column_path_presence(column))
        elif 8.0 <= az_delta <= 36.0 and distance_delta <= 80.0:
            neighbor_presence.append(column_path_presence(column))
    if not neighbor_presence:
        return 0.0
    cloudy_neighbor = max(neighbor_presence)
    return clamp((cloudy_neighbor - hole_presence) / 0.62)


def cloud_hole_path_clearance(columns: Sequence[CloudColumn], hole: CloudColumn, target: CloudColumn) -> float:
    penalties: List[float] = []
    for column in columns:
        if column is hole or column is target:
            continue
        if column.distance_km <= hole.distance_km + 8.0 or column.distance_km >= target.distance_km - 8.0:
            continue
        if angular_difference_deg(column.bearing_deg, target.bearing_deg) > 18.0:
            continue
        blocker = clamp(max(column.low_presence, 0.82 * column.mid_presence, 0.45 * column.deep_cloud_risk))
        if blocker <= 0.0:
            continue
        distance_factor = clamp(1.0 - (column.distance_km - hole.distance_km) / max(target.distance_km - hole.distance_km, 1.0))
        penalties.append(blocker * (0.55 + 0.45 * distance_factor))
    if not penalties:
        return 1.0
    return clamp(1.0 - max(penalties))


def cloud_hole_blocking_risk(column: CloudColumn) -> float:
    low_mid_block = max(column.low_presence, 0.82 * column.mid_presence)
    thick_upper_block = max(column.high_presence, column.mid_presence) * clamp(
        1.0 - column.burnability_score + 0.45 * column.thickness_risk + 0.45 * column.deep_cloud_risk
    )
    return clamp(0.66 * low_mid_block + 0.46 * column.deep_cloud_risk + 0.28 * column.thickness_risk + 0.30 * thick_upper_block)


def cloud_hole_rear_blocking_risk(columns: Sequence[CloudColumn], hole: CloudColumn, target: CloudColumn) -> float:
    risks: List[float] = []
    for column in columns:
        if column is hole or column is target:
            continue
        if column.distance_km <= hole.distance_km + 8.0 or column.distance_km >= target.distance_km - 8.0:
            continue
        if angular_difference_deg(column.bearing_deg, target.bearing_deg) > 18.0 and angular_difference_deg(column.bearing_deg, hole.bearing_deg) > 18.0:
            continue
        risk = cloud_hole_blocking_risk(column)
        if risk <= 0.0:
            continue
        distance_factor = clamp(1.0 - (column.distance_km - hole.distance_km) / max(target.distance_km - hole.distance_km, 1.0))
        risks.append(risk * (0.62 + 0.38 * distance_factor))
    target_self_risk = clamp(0.55 * target.deep_cloud_risk + 0.35 * target.thickness_risk + 0.28 * target.low_presence)
    risks.append(target_self_risk)
    return max(risks, default=0.0)


def cloud_hole_rear_target_score(
    column: CloudColumn,
    solar_elevation_deg: float,
    sector_delta_deg: float = 0.0,
    observer_elevation_m: float = 0.0,
) -> float:
    high_mid = max(column.high_presence, 0.78 * column.mid_presence)
    if high_mid <= 0.0:
        return 0.0
    required_height = required_illumination_height_m(
        column.distance_km,
        solar_elevation_deg,
        sector_delta_deg,
    )
    height_score = illumination_feasibility(column.cloud_base_m, required_height)
    distance_score = clamp(1.0 - abs(column.distance_km - 210.0) / 240.0)
    self_blocking = cloud_hole_blocking_risk(column)
    return clamp(
        high_mid
        * column.burnability_score
        * height_score
        * (0.45 + 0.55 * distance_score)
        * (1.0 - 0.65 * column.deep_cloud_risk)
        * (1.0 - 0.36 * self_blocking)
    )


def cloud_hole_lightpath_signals(
    snapshot: DomainSnapshot,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    columns: Optional[ColumnIndex] = None,
    observer_elevation_m: float = 0.0,
) -> List[CloudHoleSignal]:
    all_columns = (columns if columns is not None else build_cloud_columns(snapshot)).values()
    column_values = [
        column
        for column in all_columns
        if column.distance_km <= MAX_CANDIDATE_DISTANCE_KM and angular_difference_deg(column.bearing_deg, solar_azimuth_deg) <= 75.0
    ]

    global _native_cloud_hole_enabled
    has_satellite_probability = any(
        column.horizontal_cloud_probability is not None
        or any(
            feature.horizontal_cloud_probability is not None
            or feature.cloud_probability_given_cloud is not None
            for feature in column.layers.values()
        )
        for column in column_values
    )
    if (
        abs(observer_elevation_m) < 1e-6
        and not has_satellite_probability
        and _native_cloud_hole_enabled
        and native_available()
    ):
        try:
            return _cloud_hole_native_path(
                column_values, solar_elevation_deg, solar_azimuth_deg,
            )
        except Exception:
            _native_cloud_hole_enabled = False

    return _cloud_hole_python_path(
        column_values,
        solar_elevation_deg,
        solar_azimuth_deg,
        observer_elevation_m,
    )


def _cloud_hole_native_path(
    column_values: List[CloudColumn],
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
) -> List[CloudHoleSignal]:
    """Native Rust path for O(N³) hole×target×blocker geometry."""
    raw_signals = native_cloud_hole_signals(
        column_values, solar_elevation_deg, solar_azimuth_deg,
    )
    # Build lookup from rounded (distance, bearing) → column for cloud_type, lat, lon.
    column_lookup: Dict[Tuple[float, float], CloudColumn] = {}
    for col in column_values:
        key = (round(col.distance_km, 1), round(col.bearing_deg, 1))
        column_lookup[key] = col

    signals: List[CloudHoleSignal] = []
    for sig in raw_signals:
        (hole_dist, hole_bearing, sector_delta, clear_slot, edge_contrast,
         target_dist, target_bearing, target_score, alignment, clearance,
         rear_blocking, raw_score, burnability, deep_risk, thickness_risk) = sig

        target_col = column_lookup.get(
            (round(target_dist, 1), round(target_bearing, 1))
        )
        if target_col is None:
            continue

        score = clamp(raw_score)
        if score <= 0.22:
            continue
        signals.append(
            CloudHoleSignal(
                distance_km=hole_dist,
                bearing_deg=hole_bearing,
                sector_delta_deg=sector_delta,
                clear_slot_score=clear_slot,
                edge_contrast_score=edge_contrast,
                rear_target_distance_km=target_col.distance_km,
                rear_target_bearing_deg=target_col.bearing_deg,
                rear_target_cloud_type=target_col.cloud_type,
                rear_target_burnability_score=target_col.burnability_score,
                rear_target_score=target_score,
                alignment_score=alignment,
                path_clearance_score=clearance,
                rear_blocking_risk=rear_blocking,
                score=score,
            )
        )
    signals.sort(key=lambda item: item.score, reverse=True)
    return signals


def _cloud_hole_python_path(
    column_values: List[CloudColumn],
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    observer_elevation_m: float = 0.0,
) -> List[CloudHoleSignal]:
    """Pure Python fallback for cloud-hole lightpath scoring."""
    signals: List[CloudHoleSignal] = []
    for hole in column_values:
        if hole.distance_km < 25.0 or hole.distance_km > 260.0:
            continue
        sector_delta = angular_difference_deg(hole.bearing_deg, solar_azimuth_deg)
        if sector_delta > 32.0:
            continue
        # Minimum penetration threshold: reject holes where sunlight would need
        # to travel more than 50 km horizontally through the gap (mirrors Rust).
        cloud_thickness_m = max(hole.cloud_top_m, 500.0)
        min_penetration = _cloud_hole_min_penetration_km(
            hole.distance_km, solar_elevation_deg, sector_delta, cloud_thickness_m,
        )
        if min_penetration > 50.0:
            continue
        clear_slot = cloud_hole_clear_slot_score(hole)
        if clear_slot < 0.45:
            continue
        edge_contrast = cloud_hole_edge_contrast_score(column_values, hole)
        if edge_contrast < 0.35:
            continue

        rear_targets: List[tuple[float, CloudColumn, float, float, float, float]] = []
        for target in column_values:
            if target.distance_km <= hole.distance_km + 35.0 or target.distance_km > MAX_CANDIDATE_DISTANCE_KM:
                continue
            target_delta = angular_difference_deg(target.bearing_deg, solar_azimuth_deg)
            hole_target_delta = angular_difference_deg(target.bearing_deg, hole.bearing_deg)
            if target_delta > 38.0 or hole_target_delta > 24.0:
                continue
            target_score = cloud_hole_rear_target_score(
                target,
                solar_elevation_deg,
                target_delta,
                observer_elevation_m,
            )
            if target_score < 0.22:
                continue
            alignment = clamp(1.0 - hole_target_delta / 24.0) * clamp(1.0 - target_delta / 44.0)
            clearance = cloud_hole_path_clearance(column_values, hole, target)
            rear_blocking = cloud_hole_rear_blocking_risk(column_values, hole, target)
            if rear_blocking > 0.72:
                continue
            rear_targets.append((target_score * alignment * clearance * (1.0 - 0.62 * rear_blocking), target, target_score, alignment, clearance, rear_blocking))
        if not rear_targets:
            continue

        rear_targets.sort(key=lambda item: item[0], reverse=True)
        _, target, target_score, alignment, clearance, rear_blocking = rear_targets[0]
        distance_gap_score = clamp((target.distance_km - hole.distance_km - 30.0) / 130.0) * clamp(1.0 - (target.distance_km - hole.distance_km - 260.0) / 260.0)
        score = clamp(
            (
                0.26 * clear_slot
                + 0.22 * edge_contrast
                + 0.24 * target_score
                + 0.14 * alignment
                + 0.10 * clearance
                + 0.04 * distance_gap_score
            )
            * (1.0 - 0.50 * rear_blocking)
        )
        if score <= 0.22:
            continue
        signals.append(
            CloudHoleSignal(
                distance_km=hole.distance_km,
                bearing_deg=hole.bearing_deg,
                sector_delta_deg=sector_delta,
                clear_slot_score=clear_slot,
                edge_contrast_score=edge_contrast,
                rear_target_distance_km=target.distance_km,
                rear_target_bearing_deg=target.bearing_deg,
                rear_target_cloud_type=target.cloud_type,
                rear_target_burnability_score=target.burnability_score,
                rear_target_score=target_score,
                alignment_score=alignment,
                path_clearance_score=clearance,
                rear_blocking_risk=rear_blocking,
                score=score,
            )
        )
    signals.sort(key=lambda item: item.score, reverse=True)
    return signals


def candidate_signals(
    snapshot: DomainSnapshot,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    aerosol: AerosolReading,
    columns: Optional[ColumnIndex] = None,
    feature_index: Optional[FeatureSpatialIndex] = None,
    observer_elevation_m: float = 0.0,
) -> List[CandidateSignal]:
    # Atmospheric clarity is retained as a diagnostic. Candidate geometry and
    # material stay independent of AOD; each target carries its own integrated
    # Sun-to-cloud-to-observer path transmission.
    atmospheric_clarity = atmospheric_clarity_score(aerosol.aod550)
    signals: List[CandidateSignal] = []

    def candidate_cloud_base(feature: GridCellFeature) -> float:
        column = column_for_feature(columns, feature)
        if column is not None and column.cloud_base_m > 0.0:
            return column.cloud_base_m
        if feature_index is not None:
            return feature_index.cloud_base(feature)
        return estimated_cloud_base_m(feature)

    candidate_rows = (
        feature_index.candidate_rows(solar_elevation_deg, solar_azimuth_deg)
        if feature_index is not None
        else None
    )
    if candidate_rows is None:
        candidate_rows = []
        for feature in indexed_features(
            snapshot,
            feature_index,
            ((solar_azimuth_deg, 70.0),),
            min_distance_km=0.0,
            max_distance_km=MAX_CANDIDATE_DISTANCE_KM,
        ):
            is_local_upper = (
                feature.distance_km < LOCAL_UPPER_CANDIDATE_MAX_DISTANCE_KM
                and feature.pressure_hpa <= 600
            )
            if (
                feature.distance_km > MAX_CANDIDATE_DISTANCE_KM
                or feature.distance_km < MIN_CANDIDATE_DISTANCE_KM and not is_local_upper
            ):
                continue
            sector_delta = angular_difference_deg(feature.bearing_deg, solar_azimuth_deg)
            if sector_delta > 70.0:
                continue
            required_height = required_illumination_height_m(
                feature.distance_km,
                solar_elevation_deg,
                sector_delta,
            )
            cloud_base_m = candidate_cloud_base(feature)
            feasibility = illumination_feasibility(cloud_base_m, required_height)
            if feasibility <= 0.0:
                continue
            apparent_elevation = apparent_cloud_elevation_deg(
                feature.distance_km,
                cloud_base_m,
                observer_elevation_m,
            )
            sight_weight = line_of_sight_weight(apparent_elevation)
            if sight_weight <= 0.0:
                continue
            horizon_weight = horizon_proximity_weight(apparent_elevation)
            candidate_rows.append(
                (feature, sector_delta, required_height, feasibility, apparent_elevation, sight_weight, horizon_weight)
            )

    # The native prefilter historically returned top-height visibility. Rebuild
    # the target geometry from cloud base so the accelerated path follows the
    # same strict burn definition as the Python path.
    if candidate_rows is not None:
        normalized_rows = []
        for feature, sector_delta, required_height, _, _, _, _ in candidate_rows:
            cloud_base_m = candidate_cloud_base(feature)
            feasibility = illumination_feasibility(cloud_base_m, required_height)
            apparent_elevation = apparent_cloud_elevation_deg(
                feature.distance_km,
                cloud_base_m,
                observer_elevation_m,
            )
            sight_weight = line_of_sight_weight(apparent_elevation)
            if feasibility <= 0.0 or sight_weight <= 0.0:
                continue
            normalized_rows.append(
                (
                    feature,
                    sector_delta,
                    required_height,
                    feasibility,
                    apparent_elevation,
                    sight_weight,
                    horizon_proximity_weight(apparent_elevation),
                )
            )
        candidate_rows = normalized_rows

    native_clearances = (
        feature_index.path_clearance_weights(
            [row[0] for row in candidate_rows],
            solar_elevation_deg,
            solar_azimuth_deg,
        )
        if feature_index is not None
        else None
    )
    eligible_rows: List[
        tuple[
            tuple[GridCellFeature, float, float, float, float, float, float],
            float,
            float,
            float,
            float,
        ]
    ] = []
    aerosol_inputs: List[
        tuple[float, float, float, float, float, float, float, float]
    ] = []
    for row_index, row in enumerate(candidate_rows):
        feature, sector_delta, required_height, feasibility, apparent_elevation, sight_weight, horizon_weight = row
        clearance_weight = (
            native_clearances[row_index]
            if native_clearances is not None
            else path_clearance_weight(
                snapshot,
                feature,
                apparent_elevation,
                solar_elevation_deg,
                solar_azimuth_deg,
                feature_index,
                observer_elevation_m,
            )
        )
        if clearance_weight <= 0.05:
            continue
        observer_path_aod, inflow_path_aod, path_variability = path_aod_components(
            aerosol,
            feature.lat,
            feature.lon,
            feature.distance_km,
            feature.bearing_deg,
            solar_azimuth_deg,
        )
        eligible_rows.append(
            (row, clearance_weight, observer_path_aod, inflow_path_aod, path_variability)
        )
        aerosol_inputs.append(
            (
                aerosol.aod550,
                observer_path_aod,
                inflow_path_aod,
                feature.distance_km,
                candidate_cloud_base(feature),
                apparent_elevation,
                solar_elevation_deg,
                observer_elevation_m,
            )
        )

    try:
        aerosol_metrics = native_aerosol_transmission_batch(
            np.asarray(aerosol_inputs, dtype=np.float64).reshape((-1, 8))
        )
    except Exception:
        aerosol_metrics = np.empty((len(aerosol_inputs), 5), dtype=np.float64)
        for index, values in enumerate(aerosol_inputs):
            samples = aerosol_lightpath_transmission_samples(
                values[0],
                values[3],
                values[4],
                values[5],
                values[6],
                observer_aod550=values[1],
                inflow_aod550=values[2],
                observer_elevation_m=values[7],
            )
            aerosol_metrics[index, :3] = samples
            aerosol_metrics[index, 3] = aerosol_transmission_weight_from_samples(samples)
            aerosol_metrics[index, 4] = clamp(max(samples) - min(samples))

    for eligible_index, eligible in enumerate(eligible_rows):
        row, clearance_weight, observer_path_aod, inflow_path_aod, path_variability = eligible
        feature, sector_delta, required_height, feasibility, apparent_elevation, sight_weight, horizon_weight = row
        aerosol_weight = float(aerosol_metrics[eligible_index, 3])
        aerosol_sensitivity = float(aerosol_metrics[eligible_index, 4])
        cloud_base_m = candidate_cloud_base(feature)
        illumination_score = clamp(
            (cloud_base_m - required_height + 700.0) / 2600.0
        )
        cloud_evidence_score = feature_cloud_presence_score(feature)
        explicit_presence = explicit_cloud_presence_score(feature)
        if explicit_presence is not None and explicit_presence < 0.06:
            continue
        if (
            feature.distance_km < LOCAL_UPPER_CANDIDATE_MAX_DISTANCE_KM
            and cloud_evidence_score < 0.42
        ):
            continue
        lift_score = 0.5
        if feature.omega_pa_s is not None:
            lift_score = clamp((-feature.omega_pa_s + 0.05) / 0.5)
        azimuth_score = clamp(1.0 - sector_delta / 70.0)
        column = column_for_feature(columns, feature)
        type_burnability = column.burnability_score if column is not None else 0.5
        cloud_type = column.cloud_type if column is not None else "unknown"
        deep_risk = column.deep_cloud_risk if column is not None else 0.0
        type_factor = 0.62 + 0.38 * type_burnability
        high_suitability = (
            max(high_cloud_suitability_score(feature.rh_percent), cloud_evidence_score)
            if feature.pressure_hpa <= 400
            else 1.0
        )
        suitability_factor = 0.72 + 0.28 * high_suitability
        horizon_view_factor = 0.65 + 0.35 * horizon_weight
        # Dawn boost: at negative solar elevation, slant-path aerosol transmission
        # underestimates high-cloud illumination — compensate to keep distant candidates alive
        dawn_boost = 1.0
        if solar_elevation_deg < 0.0:
            dawn_boost = min(1.35, 1.0 + abs(solar_elevation_deg) / 15.0)
        explicit_gate = (
            1.0
            if explicit_presence is None
            else smooth_gate(explicit_presence, 0.08, 0.35)
        )
        geometric_candidate_score = (
            0.50 * cloud_evidence_score
            + 0.18 * azimuth_score
            + 0.12 * horizon_weight
            + 0.05 * distance_weight(feature.distance_km)
            + 0.15 * lift_score
        ) * pressure_weight(feature.pressure_hpa) * sight_weight * horizon_view_factor * explicit_gate * dawn_boost
        # Adaptive threshold: at negative solar elevation, lower the bar so distant
        # candidates survive (their raw aerosol weight is low due to long slant path)
        min_score = 0.15
        if solar_elevation_deg < 0.0:
            min_score = max(0.06, 0.15 + solar_elevation_deg * 0.012)
        if geometric_candidate_score <= min_score:
            continue
        score = geometric_candidate_score * type_factor * suitability_factor
        signals.append(
            CandidateSignal(
                lat=feature.lat,
                lon=feature.lon,
                pressure_hpa=feature.pressure_hpa,
                distance_km=feature.distance_km,
                bearing_deg=feature.bearing_deg,
                sector_delta_deg=sector_delta,
                apparent_elevation_deg=apparent_elevation,
                aerosol_transmission_score=aerosol_weight,
                path_clearance_score=clearance_weight,
                illumination_height_m=required_height,
                cloud_height_m=feature.geopotential_height_m,
                score=clamp(score),
                rh_percent=feature.rh_percent,
                cloud_base_m=cloud_base_m,
                cloud_type=cloud_type,
                cloud_type_burnability_score=type_burnability,
                deep_cloud_risk=deep_risk,
                aerosol_sensitivity_score=aerosol_sensitivity,
                aerosol_path_variability_score=path_variability,
                observer_path_aod550=observer_path_aod,
                inflow_path_aod550=inflow_path_aod,
                cloud_evidence_score=cloud_evidence_score,
                cloud_fraction_percent=feature.cloud_fraction_percent,
                cloud_liquid_kg_kg=feature.cloud_liquid_kg_kg,
                cloud_ice_kg_kg=feature.cloud_ice_kg_kg,
                atmospheric_clarity_score=atmospheric_clarity,
                illumination_score=illumination_score,
                footprint_azimuth_width_deg=feature.footprint_azimuth_width_deg,
                footprint_near_distance_km=feature.footprint_near_distance_km,
                footprint_far_distance_km=feature.footprint_far_distance_km,
                horizontal_cloud_probability=feature.horizontal_cloud_probability,
            )
        )
    signals.sort(key=lambda item: item.score, reverse=True)
    return signals


def spatial_bin_scores(signals: Sequence[CandidateSignal], min_score: float = BURNABLE_CELL_SCORE) -> Dict[tuple[int, int], float]:
    scores: Dict[tuple[int, int], float] = {}
    for signal in signals:
        if signal.score < min_score:
            continue
        cell = (int(signal.bearing_deg // 10.0), int(signal.distance_km // 45.0))
        scores[cell] = max(scores.get(cell, 0.0), signal.score)
    return scores


def strong_spatial_bins(signals: Sequence[CandidateSignal], min_score: float = STRONG_CELL_SCORE) -> tuple[set[tuple[int, int]], set[int], set[int]]:
    cells: set[tuple[int, int]] = set()
    azimuth_bins: set[int] = set()
    distance_bins: set[int] = set()
    for signal in signals:
        if signal.score < min_score:
            continue
        az_bin = int(signal.bearing_deg // 10.0)
        dist_bin = int(signal.distance_km // 45.0)
        cells.add((az_bin, dist_bin))
        azimuth_bins.add(az_bin)
        distance_bins.add(int(signal.distance_km // 90.0))
    return cells, azimuth_bins, distance_bins


def extent_score(signals: Sequence[CandidateSignal]) -> tuple[float, int, int]:
    burnable_cells = spatial_bin_scores(signals)
    strong_cells, _, _ = strong_spatial_bins(signals)
    azimuth_weights: Dict[int, float] = {}
    distance_weights: Dict[int, float] = {}
    weighted_cells = 0.0
    for (az_bin, dist_bin), score in burnable_cells.items():
        weight = clamp((score - BURNABLE_CELL_SCORE) / (STRONG_CELL_SCORE - BURNABLE_CELL_SCORE))
        weighted_cells += weight
        azimuth_weights[az_bin] = max(azimuth_weights.get(az_bin, 0.0), weight)
        distance_bin = int((dist_bin * 45.0) // 90.0)
        distance_weights[distance_bin] = max(distance_weights.get(distance_bin, 0.0), weight)
    cell_score = clamp(weighted_cells / 18.0)
    azimuth_score = clamp(sum(azimuth_weights.values()) / 6.0)
    distance_score = clamp(sum(distance_weights.values()) / 4.0)
    score = clamp(0.56 * cell_score + 0.28 * azimuth_score + 0.16 * distance_score)
    return score, len(burnable_cells), len(strong_cells)


def candidate_angular_cloud_coverage(
    signals: Sequence[CandidateSignal],
    solar_azimuth_deg: float,
    observer_elevation_m: float = 0.0,
) -> float:
    """Sky-view solid-angle coverage shared conceptually with national.rs.

    Coverage uses cloud presence and geometry only. Candidate strength,
    illumination, visibility, aerosol and path clearance remain independent
    quality inputs.
    """
    if not signals:
        return 0.0
    coordinates = sorted({round(signal.lat, 6) for signal in signals})
    longitude_coordinates = sorted({round(signal.lon, 6) for signal in signals})
    spacings = [
        right - left
        for values in (coordinates, longitude_coordinates)
        for left, right in zip(values, values[1:])
        if right - left > 1e-5
    ]
    spacing_deg = float(np.median(spacings)) if spacings else 0.25
    spacing_deg = clamp(spacing_deg, 0.02, 1.0)

    # One vertical cloud body per horizontal cell, matching the national grid
    # preprocessing that selects a target column before measuring sky area.
    columns: dict[tuple[float, float], dict[str, float]] = {}
    for signal in signals:
        key = (round(signal.lat, 5), round(signal.lon, 5))
        horizontal_probability = getattr(signal, "horizontal_cloud_probability", None)
        fraction = (
            clamp(float(horizontal_probability))
            if horizontal_probability is not None
            else (
                clamp(float(signal.cloud_fraction_percent) / 100.0)
                if signal.cloud_fraction_percent is not None
                else clamp(signal.cloud_evidence_score)
            )
        )
        row = columns.get(key)
        if row is None:
            columns[key] = {
                "lat": signal.lat,
                "distance": signal.distance_km,
                "bearing": signal.bearing_deg,
                "base": signal.cloud_base_m,
                "top": signal.cloud_height_m,
                "fraction": fraction,
                "footprint_width": signal.footprint_azimuth_width_deg,
                "footprint_near": signal.footprint_near_distance_km,
                "footprint_far": signal.footprint_far_distance_km,
            }
        else:
            row["base"] = min(row["base"], signal.cloud_base_m)
            row["top"] = max(row["top"], signal.cloud_height_m)
            row["fraction"] = max(row["fraction"], fraction)

    opacity = np.zeros((24, 60), dtype=np.float64)
    for row in columns.values():
        center = (row["bearing"] - solar_azimuth_deg + 180.0) % 360.0 - 180.0
        latitude = row["lat"]
        distance_km = row["distance"]
        footprint_width = row.get("footprint_width")
        footprint_near = row.get("footprint_near")
        footprint_far = row.get("footprint_far")
        has_explicit_footprint = (
            footprint_width is not None
            and footprint_near is not None
            and footprint_far is not None
        )
        local_radius_km = 0.5 * spacing_deg * math.sqrt(
            110.574 * 111.320 * max(abs(math.cos(math.radians(latitude))), 0.2)
        )
        contains_observer = (
            float(footprint_near) <= 0.0
            if has_explicit_footprint
            else distance_km <= local_radius_km
        )
        if contains_observer:
            azimuth_start, azimuth_end = -60.0, 60.0
        elif has_explicit_footprint:
            half_width = 0.5 * float(footprint_width)
            azimuth_start = max(center - half_width, -60.0)
            azimuth_end = min(center + half_width, 60.0)
        else:
            cell_width_km = spacing_deg * math.sqrt(
                110.574**2 + (111.320 * math.cos(math.radians(latitude))) ** 2
            )
            half_width = math.degrees(math.atan(0.5 * cell_width_km / max(distance_km, 1.0)))
            azimuth_start = max(center - half_width, -60.0)
            azimuth_end = min(center + half_width, 60.0)
        if azimuth_end <= azimuth_start:
            continue
        base_distance_km = (
            max(float(footprint_far), 1.0)
            if has_explicit_footprint
            else max(local_radius_km, 1.0) if contains_observer else distance_km
        )
        top_distance_km = (
            max(float(footprint_near), 1.0)
            if has_explicit_footprint
            else distance_km
        )
        base = clamp(
            apparent_cloud_elevation_deg(
                base_distance_km,
                row["base"],
                observer_elevation_m,
            ),
            0.0,
            60.0,
        )
        top = 60.0 if contains_observer else clamp(
            apparent_cloud_elevation_deg(
                top_distance_km,
                row["top"],
                observer_elevation_m,
            ),
            0.0,
            60.0,
        )
        if top <= base:
            continue
        az_start = max(0, min(24, math.floor((azimuth_start + 60.0) / 5.0)))
        az_end = max(0, min(24, math.ceil((azimuth_end + 60.0) / 5.0)))
        elevation_start = max(0, min(60, math.floor(base)))
        elevation_end = max(0, min(60, math.ceil(top)))
        for azimuth_bin in range(az_start, az_end):
            bin_start = -60.0 + azimuth_bin * 5.0
            azimuth_overlap = clamp(
                (min(azimuth_end, bin_start + 5.0) - max(azimuth_start, bin_start)) / 5.0
            )
            for elevation_bin in range(elevation_start, elevation_end):
                elevation_overlap = clamp(
                    min(top, elevation_bin + 1.0) - max(base, elevation_bin)
                )
                added = row["fraction"] * azimuth_overlap * elevation_overlap
                opacity[azimuth_bin, elevation_bin] = 1.0 - (
                    1.0 - opacity[azimuth_bin, elevation_bin]
                ) * (1.0 - added)

    elevation_radians = np.radians(np.arange(61, dtype=np.float64))
    solid_angle_weights = np.sin(elevation_radians[1:]) - np.sin(elevation_radians[:-1])
    occupied = float(np.sum(opacity * solid_angle_weights[None, :]))
    visible = 24.0 * math.sin(math.radians(60.0))
    return clamp(occupied / max(visible, 1e-9))


def cloud_column_angular_cloud_coverage(
    columns: Sequence[CloudColumn],
    solar_azimuth_deg: float,
    observer_elevation_m: float = 0.0,
) -> float:
    """Measure visible cloud area from every valid horizontal cloud footprint.

    This path deliberately does not consume candidate sorting, illumination,
    or LOS thresholds.  A column's horizontal probability is one footprint
    value; its pressure layers only provide the vertical bounds for projection.
    """
    from types import SimpleNamespace

    rows = []
    for column in columns:
        probability = column.horizontal_cloud_probability
        if probability is None:
            probability = max(
                column.high_presence,
                column.mid_presence,
                column.low_presence,
                column.explicit_cloud_evidence,
            )
        probability = clamp(float(probability))
        if probability <= 0.0:
            continue
        rows.append(
            SimpleNamespace(
                lat=column.lat,
                lon=column.lon,
                distance_km=column.distance_km,
                bearing_deg=column.bearing_deg,
                cloud_fraction_percent=None,
                horizontal_cloud_probability=probability,
                cloud_evidence_score=probability,
                cloud_base_m=column.cloud_base_m,
                cloud_height_m=column.cloud_top_m,
                footprint_azimuth_width_deg=column.footprint_azimuth_width_deg,
                footprint_near_distance_km=column.footprint_near_distance_km,
                footprint_far_distance_km=column.footprint_far_distance_km,
            )
        )
    return candidate_angular_cloud_coverage(rows, solar_azimuth_deg, observer_elevation_m)


def observer_local_cloud_field_amount_from_columns(columns: Sequence[CloudColumn]) -> float:
    """Return the complete cloud probability of the footprint containing the observer."""
    local_probabilities = []
    for column in columns:
        contains_observer = (
            column.footprint_near_distance_km is not None
            and float(column.footprint_near_distance_km) <= 0.0
        ) or (
            column.footprint_near_distance_km is None
            and column.distance_km <= 1.0
        )
        if not contains_observer:
            continue
        probability = column.horizontal_cloud_probability
        if probability is None:
            probability = max(
                column.high_presence,
                column.mid_presence,
                column.low_presence,
                column.explicit_cloud_evidence,
            )
        local_probabilities.append(clamp(float(probability)))
    return max(local_probabilities, default=0.0)


def vividness_score(signals: Sequence[CandidateSignal], extent: float) -> float:
    if not signals:
        return 0.0
    strong = [signal.score for signal in signals if signal.score >= 0.58]
    if not strong:
        strong = [signals[0].score]
    core_strength = max(strong)
    mean_strength = mean(strong[: min(12, len(strong))])
    # Vividness describes the target cloud itself. Aerosol and observer
    # visibility are independent path terms and are applied by their own gates.
    return clamp(0.52 * core_strength + 0.30 * mean_strength + 0.18 * extent)


def assess_timestep(
    snapshot: DomainSnapshot,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    aerosol: AerosolReading,
    include_map_cells: bool = False,
    map_radius_km: float = MAX_ANALYSIS_RADIUS_KM,
    observer_elevation_m: float = 0.0,
) -> TimeStepAssessment:
    from firesky.cross_section import diagnose_cross_section

    column_radius_km = max(MAX_ANALYSIS_RADIUS_KM, map_radius_km if include_map_cells else 0.0)
    feature_index = FeatureSpatialIndex(
        snapshot.features,
        diagnostic_radius_km=column_radius_km,
        observer_elevation_m=observer_elevation_m,
    )
    columns = build_cloud_columns(snapshot, feature_index, column_radius_km)
    cross_section = diagnose_cross_section(
        snapshot,
        solar_elevation_deg,
        solar_azimuth_deg,
        columns,
        observer_elevation_m,
    )
    signals = candidate_signals(
        snapshot,
        solar_elevation_deg,
        solar_azimuth_deg,
        aerosol,
        columns,
        feature_index,
        observer_elevation_m,
    )
    local_deck_signals = local_deck_edge_signals(
        snapshot,
        solar_elevation_deg,
        solar_azimuth_deg,
        aerosol,
        columns,
        observer_elevation_m,
    )
    remote_boundary_signals = boundary_lightpath_signals(
        snapshot,
        solar_elevation_deg,
        solar_azimuth_deg,
        aerosol,
        columns,
        feature_index,
        observer_elevation_m,
    )
    boundary_signals = sorted(
        [*local_deck_signals, *remote_boundary_signals],
        key=lambda item: item.score,
        reverse=True,
    )
    cloud_hole_signals = cloud_hole_lightpath_signals(
        snapshot,
        solar_elevation_deg,
        solar_azimuth_deg,
        columns,
        observer_elevation_m,
    )
    top = signals[0] if signals else None
    solar_inflow_blockage = diagnose_solar_inflow_blockage(
        snapshot,
        top,
        solar_elevation_deg,
        solar_azimuth_deg,
        feature_index,
        observer_elevation_m,
    )
    top_boundary = boundary_signals[0] if boundary_signals else None
    top_local_deck = local_deck_signals[0] if local_deck_signals else None
    top_remote_boundary = remote_boundary_signals[0] if remote_boundary_signals else None
    top_cloud_hole = cloud_hole_signals[0] if cloud_hole_signals else None
    cloud_penalty = low_cloud_penalty(snapshot, solar_azimuth_deg, feature_index)
    weather_penalty, surface_visibility_m, precip_rate = surface_weather_penalty(snapshot, feature_index)
    low_penalty = clamp(1.0 - (1.0 - cloud_penalty) * (1.0 - weather_penalty))
    signal_strength = top.score if top else 0.0
    visibility = clamp(1.0 - low_penalty)
    structure_extent, burning_cloud_count, strong_burning_cloud_count = extent_score(signals)
    forward_view_cloud_field_amount = cloud_column_angular_cloud_coverage(
        list(columns.values()),
        solar_azimuth_deg,
        observer_elevation_m,
    )
    forward_burnable_cloud_field_amount = candidate_angular_cloud_coverage(
        signals,
        solar_azimuth_deg,
        observer_elevation_m,
    )
    observer_local_cloud_field_amount = observer_local_cloud_field_amount_from_columns(
        list(columns.values())
    )
    # raw_extent 语义与 forward_burnable_cloud_field_amount 完全一致（同参数、
    # 纯函数、中间无状态变更）：直接复用，避免每扫描重复计算一次立体角网格。
    raw_extent = forward_burnable_cloud_field_amount
    edge_structure_gate = 1.0
    if structure_extent >= 0.45:
        if top_boundary is None:
            edge_structure_gate = 0.55
        else:
            edge_structure_gate = clamp(0.55 + 0.45 * top_boundary.edge_contrast_score)
    # Keep coverage geometric. Cloud uniformity is a separate structure term;
    # folding it into coverage would make the final quality core penalize the
    # same structure twice.
    structured_extent = raw_extent
    vividness = vividness_score(signals, structured_extent)
    solar_weight = solar_elevation_weight(solar_elevation_deg)
    ground_visibility = visibility_gate(visibility)
    visible_extent = clamp(structured_extent * ground_visibility)
    boundary_component = top_boundary.score if top_boundary else 0.0
    local_deck_component = top_local_deck.score if top_local_deck else 0.0
    remote_boundary_component = top_remote_boundary.score if top_remote_boundary else 0.0
    cloud_hole_component = top_cloud_hole.score if top_cloud_hole else 0.0
    profile_integrity = cross_section.lightpath_integrity_score
    if cross_section.samples_count == 0:
        # 太阳方向没有网格样本，无法诊断光路——缺数据不等于被挡，给中性值，
        # 不用它做否决，交由其它必要条件把关。
        profile_factor = 0.70
    else:
        # 光路通透：必要否决项。去掉旧的 0.55 地板——光路被挡死时必须能压到接近 0。
        # 宽松比例映射：完整度主导，透过率做小幅修正；完整度低于约 0.28 视为基本挡死。
        profile_factor = clamp((profile_integrity - 0.28) / 0.72)
    remote_candidates = {
        "distant_target": signal_strength,
        "remote_boundary": remote_boundary_component,
        "cloud_hole": cloud_hole_component,
    }
    remote_regime, remote_physical_score = max(
        remote_candidates.items(), key=lambda item: item[1]
    )
    local_regime_wins = local_deck_component > remote_physical_score
    selected_regime = "local_deck" if local_regime_wins else remote_regime
    physical_existence_score = max(local_deck_component, remote_physical_score)

    # Existence is now controlled by one physically coherent regime. Extent,
    # visibility and vividness can grade a valid event, but cannot create one.
    if local_regime_wins and top_local_deck is not None:
        weather_visibility = clamp(1.0 - weather_penalty)
        regime_visibility_gate = visibility_gate(weather_visibility)
        local_geometric_extent = clamp(
            top_local_deck.local_cloud_presence_score
            * (0.65 + 0.35 * top_local_deck.edge_contrast_score)
        )
        structured_extent = max(structured_extent, local_geometric_extent)
        visible_extent = max(
            visible_extent,
            local_geometric_extent * regime_visibility_gate,
        )
        local_vividness = clamp(
            0.65 * top_local_deck.local_cloud_presence_score
            + 0.35 * top_local_deck.edge_contrast_score
        )
        vividness = max(vividness, local_vividness)
        regime_profile_factor = 1.0
        burning_cloud_count = max(1, burning_cloud_count)
    else:
        regime_visibility_gate = ground_visibility
        regime_profile_factor = profile_factor
        if top is not None:
            regime_profile_factor = min(
                regime_profile_factor,
                top.path_clearance_score,
            )

    if local_regime_wins and top_local_deck is not None:
        target_deep_cloud_risk = top_local_deck.deep_cloud_risk
    elif selected_regime == "remote_boundary" and top_remote_boundary is not None:
        target_deep_cloud_risk = top_remote_boundary.deep_cloud_risk
    else:
        target_deep_cloud_risk = top.deep_cloud_risk if top is not None else 0.0
    deep_cloud_gate = 1.0 - smooth_gate(target_deep_cloud_risk, 0.32, 0.62)
    structure_gate_score = min(edge_structure_gate, deep_cloud_gate)
    regime_profile_factor = min(regime_profile_factor, structure_gate_score)

    observer_lat = aerosol.observer_lat
    if observer_lat is None and snapshot.features:
        observer_lat = snapshot.features[0].lat
    if observer_lat is None:
        observer_lat = 30.0
    selected_aod_observer = max(0.0, float(aerosol.aod550))
    selected_aod_inflow = selected_aod_observer
    selected_observer_path_aod550: float | None = None
    selected_inflow_path_aod550: float | None = None
    selected_aerosol_path_variability_score = 0.0
    selected_cloud_base_m = 0.0
    selected_distance_km = 0.0
    selected_path_transmission = 1.0
    if local_regime_wins and top_local_deck is not None:
        selected_observer_path_aod550 = top_local_deck.observer_path_aod550
        selected_inflow_path_aod550 = top_local_deck.inflow_path_aod550
        selected_aerosol_path_variability_score = top_local_deck.aerosol_path_variability_score
        selected_aod_observer = max(
            selected_aod_observer,
            float(top_local_deck.observer_path_aod550 or 0.0),
        )
        selected_aod_inflow = float(top_local_deck.inflow_path_aod550 or selected_aod_observer)
        selected_cloud_base_m = float(top_local_deck.cloud_base_m)
        selected_distance_km = float(top_local_deck.distance_km)
        selected_path_transmission = float(top_local_deck.aerosol_transmission_score)
    elif selected_regime == "remote_boundary" and top_remote_boundary is not None:
        selected_observer_path_aod550 = top_remote_boundary.observer_path_aod550
        selected_inflow_path_aod550 = top_remote_boundary.inflow_path_aod550
        selected_aerosol_path_variability_score = top_remote_boundary.aerosol_path_variability_score
        selected_aod_observer = float(top_remote_boundary.observer_path_aod550 or selected_aod_observer)
        selected_aod_inflow = float(top_remote_boundary.inflow_path_aod550 or selected_aod_observer)
        selected_cloud_base_m = float(top_remote_boundary.cloud_base_m)
        selected_distance_km = float(top_remote_boundary.distance_km)
        selected_path_transmission = float(top_remote_boundary.aerosol_transmission_score)
    elif top is not None:
        selected_observer_path_aod550 = top.observer_path_aod550
        selected_inflow_path_aod550 = top.inflow_path_aod550
        selected_aerosol_path_variability_score = top.aerosol_path_variability_score
        selected_aod_observer = float(top.observer_path_aod550 or selected_aod_observer)
        selected_aod_inflow = float(top.inflow_path_aod550 or selected_aod_observer)
        selected_cloud_base_m = float(top.cloud_base_m)
        selected_distance_km = float(top.distance_km)
        selected_path_transmission = float(top.aerosol_transmission_score)

    # This legacy scene path already integrates both aerosol legs for its chosen
    # target. Keep that result as the sole optical correction. The authoritative
    # GFS/ECMWF point and regional path uses the all-cloud native implementation.
    path_illumination_factor = 0.70 + 0.30 * clamp(selected_path_transmission)
    effective_base_m = selected_cloud_base_m
    geometric_reach_km = cloud_edge_max_distance_km(
        effective_base_m,
        observer_elevation_m,
    )
    if selected_cloud_base_m > 0.0 and geometric_reach_km > 0.0:
        duration_min = estimate_legacy_glow_duration_minutes(
            geometric_reach_km,
            selected_distance_km,
            observer_lat,
        )
    else:
        duration_min = 0.0
    duration_factor = duration_quality_factor(duration_min)

    scoring_vividness = vividness
    burn_score = combine_regime_burn_score(
        physical_existence_score,
        structured_extent,
        vividness,
        solar_weight,
        regime_visibility_gate,
        regime_profile_factor,
        duration_factor,
        path_illumination_factor,
    )
    intensity = combine_regime_intensity_score(
        physical_existence_score,
        structured_extent,
        vividness,
        solar_weight,
        regime_visibility_gate,
        regime_profile_factor,
        path_illumination_factor,
    )
    vividness = clamp(vividness)
    convective_risk = max(
        cross_section.deep_cloud_risk,
        top.deep_cloud_risk if top is not None else 0.0,
    )
    scene = scene_diagnosis(
        local_deck=local_deck_component,
        distant_target=signal_strength,
        remote_boundary=remote_boundary_component,
        cloud_hole=cloud_hole_component,
        convective_risk=convective_risk,
    )
    phase = cloud_phase_diagnosis(
        top.cloud_ice_kg_kg if top is not None else None,
        top.cloud_liquid_kg_kg if top is not None else None,
    )
    color = color_diagnosis(
        burn_score=burn_score,
        vividness_score=vividness,
        solar_elevation_deg=solar_elevation_deg,
        aod550=aerosol.aod550,
        solar_incoming_illumination_score=selected_path_transmission,
        deep_cloud_risk=convective_risk,
        cloud_phase=str(phase["label"]),
    )
    boundary_uncertainty = None
    if local_regime_wins and top_local_deck is not None:
        boundary_uncertainty = local_deck_boundary_uncertainty(
            top_local_deck,
            solar_elevation_deg,
            observer_lat,
            visible_extent,
            scoring_vividness,
            solar_weight,
            regime_visibility_gate,
            regime_profile_factor,
            burn_score,
            path_illumination_factor,
            observer_elevation_m=observer_elevation_m,
        )
    reasons: List[str] = []
    reasons.append(
        f'主导物理场景为 {selected_regime}，物理成立度 {physical_existence_score * 100:.0f}/100；'
        '面积、鲜艳度和地面通视只用于修正已成立场景，不再相互叠加制造存在判断。'
    )
    necessary_gates = {
        "存在": soft_knee(physical_existence_score),
        "受光窗口": solar_weight,
        "地面视线": regime_visibility_gate,
        "光路通透": regime_profile_factor,
    }
    weakest_name, weakest_value = min(necessary_gates.items(), key=lambda kv: kv[1])
    reasons.append(
        "必要条件闸门（短板定天花板）："
        + "，".join(f"{name} {value * 100:.0f}" for name, value in necessary_gates.items())
        + f"；短板为「{weakest_name}」{weakest_value * 100:.0f}/100，"
        + (
            "各必要条件均成立，可烧度由质量项在天花板内决定。"
            if weakest_value >= 0.30
            else "该短板过低，已判定这片云基本烧不起来。"
        )
    )
    reasons.append(
        f"质量修正：可见燃烧窗口 {duration_min:.1f} 分钟，持续时间系数 {duration_factor:.3f}；"
        f"本地 AOD {aerosol.aod550:.3f} 仅作来源诊断；"
        f"观测/入射路径 AOD {selected_aod_observer:.3f}/{selected_aod_inflow:.3f}，"
        f"双段逐目标光路系数 {path_illumination_factor:.3f}，不再追加全场 AOD 乘数。"
    )
    if effective_base_m > 0.0:
        reasons.append(
            f"云底约 {effective_base_m:.0f} 米，按地球曲率可达约 {geometric_reach_km:.0f} 公里；"
            "该距离是目标云的几何上限，不代表范围内所有云都可烧。"
        )
    if top_local_deck is not None:
        reasons.append(
            f'本地云幕推导：{layer_label(top_local_deck.layer)}存在证据 '
            f'{top_local_deck.local_cloud_presence_score * 100:.0f}/100，太阳侧云边界约 '
            f'{top_local_deck.distance_km:.0f} 公里；当前同高度光线回穿距离约 '
            f'{top_local_deck.geometric_reach_km:.0f} 公里，云底 {top_local_deck.cloud_base_m:.0f} 米，'
            f'地影要求 {top_local_deck.required_base_m:.0f} 米。'
        )
        if boundary_uncertainty is not None:
            reasons.append(
                f'模式云/晴格点把边界夹在 {boundary_uncertainty.lower_distance_km:.0f}–'
                f'{boundary_uncertainty.upper_distance_km:.0f} 公里，区间内最终分数为 '
                f'{boundary_uncertainty.score_min * 100:.0f}–'
                f'{boundary_uncertainty.score_max * 100:.0f}/100；'
                + (
                    '当前事件判断在该区间内稳定。'
                    if boundary_uncertainty.classification_stable
                    else '当前事件判断会在该区间内翻转。'
                )
            )
        reasons.append(
            f'本地云幕真实550nm总透过率范围约 '
            f'{top_local_deck.physical_transmission_min:.4f}–{top_local_deck.physical_transmission_max:.4f}，'
            f'保守透过评分 {top_local_deck.aerosol_transmission_score * 100:.0f}/100，'
            f'入射光路净空 {top_local_deck.path_clearance_score * 100:.0f}/100。'
        )
    if top:
        reasons.append(
            f'最佳云目标位于约 {top.distance_km:.0f} 公里、{top.pressure_hpa} hPa，单点评分 {top.score:.2f}，相对湿度 {top.rh_percent:.0f}%，近似云型为 {top.cloud_type}。'
        )
        if top.cloud_fraction_percent is not None:
            reasons.append(
                f'原生云物理诊断：云量 {top.cloud_fraction_percent:.0f}%，'
                f'云水混合比 {(top.cloud_liquid_kg_kg or 0.0):.2e} kg/kg，'
                f'云冰混合比 {(top.cloud_ice_kg_kg or 0.0):.2e} kg/kg，'
                f'融合云存在证据 {top.cloud_evidence_score * 100:.0f}/100。'
            )
        else:
            reasons.append(
                f'当前模式该气压层没有原生云量/凝结物场，使用相对湿度估算云存在，证据 {top.cloud_evidence_score * 100:.0f}/100。'
            )
        if top.cloud_type_burnability_score < 0.45:
            reasons.append('该云型可烧性偏低，可能更像厚云盾、低云或弱云，已下调候选云评分。')
        elif top.cloud_type_burnability_score >= 0.75:
            reasons.append('该云型对霞光较友好，具备较好的染色潜力。')
        if top.deep_cloud_risk > 0.55:
            reasons.append('目标云柱多层湿度叠加偏强，存在厚云盾或降水云系风险。')
        if top.cloud_base_m > top.illumination_height_m:
            reasons.append('云底高于估算的受光阈值，具备被夕阳或晨光照亮的条件；云顶用于判断地面是否看得到。')
        else:
            reasons.append('云底接近或低于受光阈值，成霞时间会比较敏感；云顶高度不再替代云底参与受光判断。')
        if top.apparent_elevation_deg < 0.8:
            reasons.append('目标云在地平线上方仰角很低，远距离视线路径会削弱地面可见效果。')
        if top.aerosol_transmission_score < 0.7:
            reasons.append('目标云视仰角较低或气溶胶偏多，视线路径消光会削弱亮度和饱和度。')
        if top.aerosol_sensitivity_score >= 0.12:
            reasons.append('气溶胶垂直分布敏感性较高，不同尺度高度假设会明显改变光路透过率。')
        if top.aerosol_path_variability_score >= 0.30:
            reasons.append('沿程 AOD 水平起伏较大，局地洁净空气不能代表完整入射与观测光路。')
        if top.path_clearance_score < 0.7:
            reasons.append('目标云与太阳/观测点之间存在疑似挡光云层，光路完整性偏差。')
    else:
        reasons.append('在霞光方向上没有找到足够强的中高云目标。')
    if top_boundary:
        if top_boundary.regime == "local_deck":
            reasons.append(
                f'当前最佳边界属于本地云幕场景，物理评分 {top_boundary.score:.2f}，'
                f'不再把近场云直接当作地平线遮挡。'
            )
        else:
            reasons.append(
                f'{layer_label(top_boundary.layer)}云边界位于约 {top_boundary.distance_km:.0f} 公里，云底约 {top_boundary.cloud_base_m:.0f} 米，'
                f'受光阈值约 {top_boundary.required_base_m:.0f} 米，边界光路评分 {top_boundary.score:.2f}，近似云型为 {top_boundary.cloud_type}。'
            )
        if top_boundary.cloud_type_burnability_score < 0.45:
            reasons.append('云边界对应云型可烧性偏低，边界评分已按云型风险下调。')
        if top_boundary.deep_cloud_risk > 0.55:
            reasons.append('云边界附近存在深厚云系风险，可能发灰或被降水云盾压暗。')
        if top_boundary.edge_contrast_score < 0.35:
            reasons.append('云区同层湿度梯度偏弱，更像连续云盾而不是清晰云边界。')
        if top_boundary.clear_slot_score < 0.65:
            reasons.append('太阳方向清空槽不够完整，边界受光通道存在风险。')
        if top_boundary.path_clearance_score < 0.65:
            reasons.append('云边界前方存在疑似挡光云，边界型火烧云不确定性较高。')
        if top_boundary.thickness_score < 0.75:
            reasons.append('目标附近多层云叠加偏厚，云体可能发灰或压暗。')
    elif raw_extent >= 0.45:
        reasons.append('候选云虽多，但没有识别到明确云边界，大面积成霞度已按连续云盾风险下调。')
    if top_cloud_hole:
        reasons.append(
            f'云洞透光信号位于约 {top_cloud_hole.distance_km:.0f} 公里，清空槽 {top_cloud_hole.clear_slot_score * 100:.0f}/100，'
            f'洞边界对比 {top_cloud_hole.edge_contrast_score * 100:.0f}/100；后方约 {top_cloud_hole.rear_target_distance_km:.0f} 公里存在'
            f'{top_cloud_hole.rear_target_cloud_type}目标云，云洞-目标对齐度 {top_cloud_hole.alignment_score * 100:.0f}/100，'
            f'后方挡光风险 {top_cloud_hole.rear_blocking_risk * 100:.0f}/100。'
        )
        if top_cloud_hole.rear_blocking_risk > 0.45:
            reasons.append('云洞后方存在一定厚云或中低云遮挡，后方云可能部分挡光而不是充分受光。')
        if top_cloud_hole.path_clearance_score < 0.65:
            reasons.append('云洞后方通道仍有中低云或厚云风险，透光到后方云的确定性偏低。')
        elif top_cloud_hole.rear_target_score >= 0.55:
            reasons.append('云洞后方目标云可烧性较好，属于“洞后云被打亮”的有利结构。')
    if solar_inflow_blockage is not None and solar_inflow_blockage.score >= 0.18:
        reasons.append(
            f'远距离太阳入射挡光风险 {solar_inflow_blockage.score * 100:.0f}/100：约 {solar_inflow_blockage.distance_km:.0f} 公里、'
            f'{solar_inflow_blockage.pressure_hpa} hPa 云柱穿过太阳到目标云的入射光线，已下调存在分数和强度。'
        )
    reasons.extend(cross_section.summary)
    if signals and strong_burning_cloud_count == 0 and burning_cloud_count > 0:
        reasons.append('存在弱到中等的可照亮云目标，但还没有达到强成霞格子的阈值，更像有霞光潜力而非大规模火烧云。')
    elif burning_cloud_count <= 2 and signals:
        reasons.append('目前更像局地点烧，可被照亮的云覆盖范围偏小，不像大面积火烧云。')
    elif burning_cloud_count <= 5 and signals:
        reasons.append('可烧云区有一定规模，但还不算大范围铺开。')
    elif burning_cloud_count >= 10:
        reasons.append('可烧云区覆盖较广，具备形成大面积火烧云的条件。')
    reasons.append(f'地面可见的大面积成霞度约 {visible_extent * 100:.0f}/100，鲜艳度约 {vividness * 100:.0f}/100。')
    if low_penalty > 0.4:
        reasons.append('观测点附近低层湿度偏高，地平线附近可能被低云或雾霾遮挡。')
    if surface_visibility_m is not None:
        reasons.append(
            f'地面天气诊断：模式能见度约 {surface_visibility_m / 1000.0:.1f} 公里，'
            f'小时雨强约 {(precip_rate or 0.0) * 3600.0:.1f} 毫米。'
        )
    if weather_penalty >= 0.35:
        reasons.append('地面能见度偏低或存在降水，即使高空云被照亮，实际观测效果也会明显受限。')
    if signals and ground_visibility <= 0.05:
        reasons.append('近场低云遮挡过强，即使高空有可受光云，地面也很难看到完整霞光。')
    if solar_weight < 0.5 and solar_elevation_deg > 8.0:
        reasons.append('太阳高度仍偏高，尚未进入晚霞有效受光窗口。')
    elif solar_weight < 0.5:
        reasons.append('太阳高度已经明显偏低，云层进入地影后的成霞可能性快速下降。')
    if aerosol.quality == 'fallback':
        reasons.append('当前气溶胶使用的是回退值，置信度会偏低。')
    map_cells = build_map_cloud_cells(columns, signals, map_radius_km) if include_map_cells else []
    return TimeStepAssessment(
        forecast_hour=snapshot.forecast_hour,
        valid_time_utc=snapshot.valid_time_utc,
        valid_time_local=snapshot.valid_time_utc,
        solar_elevation_deg=solar_elevation_deg,
        solar_azimuth_deg=solar_azimuth_deg,
        burn_score=burn_score,
        intensity_score=intensity,
        extent_score=structured_extent,
        vividness_score=vividness,
        visibility_score=visibility,
        low_cloud_penalty=low_penalty,
        candidate_count=len(signals),
        burning_cloud_count=burning_cloud_count,
        top_candidate=top,
        strong_burning_cloud_count=strong_burning_cloud_count,
        top_boundary=top_boundary,
        boundary_score=boundary_component,
        boundary_uncertainty=boundary_uncertainty,
        top_cloud_hole=top_cloud_hole,
        cloud_hole_score=cloud_hole_component,
        cross_section=cross_section,
        solar_inflow_blockage=solar_inflow_blockage,
        selected_regime=str(scene["selected_regime"]),
        scene_scores=dict(scene["scene_scores"]),
        local_sky_score=float(scene["local_sky_score"]),
        remote_sky_score=float(scene["remote_sky_score"]),
        convective_risk=float(scene["convective_risk"]),
        color_diagnosis={**color, "phase": phase},
        reasons=reasons,
        map_cells=map_cells,
        geometric_cloud_coverage=structured_extent,
        structure_gate_score=structure_gate_score,
        selected_observer_path_aod550=selected_observer_path_aod550,
        selected_inflow_path_aod550=selected_inflow_path_aod550,
        selected_aerosol_path_variability_score=selected_aerosol_path_variability_score,
        forward_view_cloud_field_amount=forward_view_cloud_field_amount,
        forward_burnable_cloud_field_amount=forward_burnable_cloud_field_amount,
        observer_local_cloud_field_amount=observer_local_cloud_field_amount,
    )


def build_map_cloud_cells(
    columns: ColumnIndex,
    signals: Sequence[CandidateSignal],
    max_distance_km: float = 900.0,
) -> List[MapCloudCell]:
    signal_by_point: Dict[tuple[float, float], CandidateSignal] = {}
    for signal in signals:
        key = (round(signal.lat, 4), round(signal.lon, 4))
        current = signal_by_point.get(key)
        if current is None or signal.score > current.score:
            signal_by_point[key] = signal

    cells: List[MapCloudCell] = []
    for column in columns.values():
        if column.distance_km > max_distance_km:
            continue
        cloud_evidence = max(
            column.high_presence,
            column.mid_presence,
            column.low_presence,
            column.explicit_cloud_evidence,
        )
        signal = signal_by_point.get((round(column.lat, 4), round(column.lon, 4)))
        burn_score = signal.score if signal is not None else 0.0
        if cloud_evidence < 0.10 and burn_score < 0.15:
            continue
        cells.append(
            MapCloudCell(
                lat=column.lat,
                lon=column.lon,
                distance_km=column.distance_km,
                bearing_deg=column.bearing_deg,
                high_cloud=column.high_presence,
                mid_cloud=column.mid_presence,
                low_cloud=column.low_presence,
                cloud_evidence=cloud_evidence,
                cloud_type=column.cloud_type,
                cloud_base_m=column.cloud_base_m,
                cloud_top_m=column.cloud_top_m,
                deep_cloud_risk=column.deep_cloud_risk,
                burn_score=burn_score,
                illumination_score=(
                    illumination_feasibility(signal.cloud_base_m, signal.illumination_height_m)
                    if signal is not None
                    else 0.0
                ),
                path_clearance_score=signal.path_clearance_score if signal is not None else 0.0,
                aerosol_transmission_score=signal.aerosol_transmission_score if signal is not None else 0.0,
                blocking_risk=clamp(max(column.low_presence, 0.70 * column.deep_cloud_risk)),
                atmospheric_clarity_score=(
                    signal.atmospheric_clarity_score if signal is not None else 1.0
                ),
            )
        )
    return cells


def duration_minutes(assessments: Sequence[TimeStepAssessment], threshold: float = 0.55) -> int:
    ordered = sorted(assessments, key=lambda step: step.valid_time_utc)
    if len(ordered) < 2:
        return 0
    duration_seconds = 0.0
    for start, end in zip(ordered, ordered[1:]):
        interval_seconds = (end.valid_time_utc - start.valid_time_utc).total_seconds()
        if interval_seconds <= 0.0:
            continue
        start_above = start.burn_score >= threshold
        end_above = end.burn_score >= threshold
        if start_above and end_above:
            duration_seconds += interval_seconds
            continue
        if start_above == end_above:
            continue
        score_delta = end.burn_score - start.burn_score
        if abs(score_delta) < 1e-9:
            continue
        crossing_fraction = clamp((threshold - start.burn_score) / score_delta)
        above_fraction = crossing_fraction if start_above else 1.0 - crossing_fraction
        duration_seconds += interval_seconds * above_fraction
    return max(0, int(round(duration_seconds / 60.0)))
