from __future__ import annotations

import math

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from firesky.analysis_constants import DEFAULT_ANALYSIS_RADIUS_KM
from firesky.version import (
    NOWCAST_ALGORITHM_VERSION,
    NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION,
)

@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    elevation_m: float = 0.0


@dataclass(frozen=True)
class ForecastRequest:
    target_date: date
    timezone_name: str
    point: GeoPoint
    phenomenon: str = "dusk"
    name: str = "Observer"
    radius_km: float = DEFAULT_ANALYSIS_RADIUS_KM
    aod550_override: Optional[float] = None
    gfs_cycle_override: Optional[datetime] = None
    model_source: str = "gfs"
    forecast_mode: str = "model"
    reference_time_utc: Optional[datetime] = None
    satellite_source: str = "auto"
    include_map_cells: bool = False
    compute_backend: str = "auto"
    gfs_resolution: str = "0p25"
    # Identifies the fixed model/CAMS domain used by the unified model path.
    # It is deliberately part of the request identity because the same point
    # can be computed from overlapping named regions with different aerosol
    # footprints.
    grid_region: str = ""
    # Source-neutral cycle override used by model adapters other than GFS.
    # Keep gfs_cycle_override unchanged for the existing GFS contract.
    model_cycle_override: Optional[datetime] = None


@dataclass(frozen=True)
class SolarSnapshot:
    valid_time_utc: datetime
    valid_time_local: datetime
    elevation_deg: float
    azimuth_deg: float


@dataclass(frozen=True)
class GlowGeometryWindow:
    """Earth-shadow eligibility interval; its midpoint is not a visual peak."""

    regime: str
    start_time_utc: datetime
    start_time_local: datetime
    midpoint_time_utc: datetime
    midpoint_time_local: datetime
    end_time_utc: datetime
    end_time_local: datetime
    duration_minutes: float
    shadow_speed_km_per_min: float
    edge_distance_km: float
    cloud_base_m: float
    geometric_max_distance_km: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime,
            "start_time_utc": self.start_time_utc.isoformat(),
            "start_time_local": self.start_time_local.isoformat(),
            "midpoint_time_utc": self.midpoint_time_utc.isoformat(),
            "midpoint_time_local": self.midpoint_time_local.isoformat(),
            "end_time_utc": self.end_time_utc.isoformat(),
            "end_time_local": self.end_time_local.isoformat(),
            "duration_minutes": self.duration_minutes,
            "shadow_speed_km_per_min": self.shadow_speed_km_per_min,
            "edge_distance_km": self.edge_distance_km,
            "cloud_base_m": self.cloud_base_m,
            "geometric_max_distance_km": self.geometric_max_distance_km,
        }


@dataclass(frozen=True)
class BoundaryUncertainty:
    regime: str
    source: str
    nominal_distance_km: float
    lower_distance_km: float
    upper_distance_km: float
    width_km: float
    score_min: float
    score_max: float
    event_threshold: float
    classification_stable: bool
    nearest_flip_displacement_km: Optional[float] = None
    start_time_earliest_local: Optional[datetime] = None
    start_time_latest_local: Optional[datetime] = None
    midpoint_time_earliest_local: Optional[datetime] = None
    midpoint_time_latest_local: Optional[datetime] = None
    end_time_earliest_local: Optional[datetime] = None
    end_time_latest_local: Optional[datetime] = None
    duration_min_minutes: Optional[float] = None
    duration_max_minutes: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        def timestamp(value: Optional[datetime]) -> Optional[str]:
            return value.isoformat() if value is not None else None

        return {
            "regime": self.regime,
            "source": self.source,
            "nominal_distance_km": self.nominal_distance_km,
            "lower_distance_km": self.lower_distance_km,
            "upper_distance_km": self.upper_distance_km,
            "width_km": self.width_km,
            "score_min": self.score_min,
            "score_max": self.score_max,
            "event_threshold": self.event_threshold,
            "classification_stable": self.classification_stable,
            "nearest_flip_displacement_km": self.nearest_flip_displacement_km,
            "start_time_earliest_local": timestamp(self.start_time_earliest_local),
            "start_time_latest_local": timestamp(self.start_time_latest_local),
            "midpoint_time_earliest_local": timestamp(self.midpoint_time_earliest_local),
            "midpoint_time_latest_local": timestamp(self.midpoint_time_latest_local),
            "end_time_earliest_local": timestamp(self.end_time_earliest_local),
            "end_time_latest_local": timestamp(self.end_time_latest_local),
            "duration_min_minutes": self.duration_min_minutes,
            "duration_max_minutes": self.duration_max_minutes,
        }


@dataclass(frozen=True)
class GridCellFeature:
    lat: float
    lon: float
    distance_km: float
    bearing_deg: float
    pressure_hpa: int
    geopotential_height_m: float
    rh_percent: float
    temperature_k: Optional[float]
    omega_pa_s: Optional[float]
    u_ms: Optional[float]
    v_ms: Optional[float]
    cloud_fraction_percent: Optional[float] = None
    cloud_liquid_kg_kg: Optional[float] = None
    cloud_ice_kg_kg: Optional[float] = None
    source_lat: Optional[float] = None
    source_lon: Optional[float] = None
    source_distance_km: Optional[float] = None
    source_bearing_deg: Optional[float] = None
    surface_visibility_m: Optional[float] = None
    precip_rate_kg_m2_s: Optional[float] = None
    # 2 m temperature / dew point (K) from the GFS surface slice. They power
    # the surface-parcel LCL cloud-base first guess; None keeps the legacy
    # fixed layer-depth table.
    surface_temperature_k: Optional[float] = None
    surface_dewpoint_k: Optional[float] = None
    footprint_azimuth_width_deg: Optional[float] = None
    footprint_near_distance_km: Optional[float] = None
    footprint_far_distance_km: Optional[float] = None
    # Satellite retrievals keep horizontal cloud occupancy separate from the
    # conditional vertical profile P(height | cloud).  Native model features
    # leave these unset and continue to use cloud_fraction_percent directly.
    horizontal_cloud_probability: Optional[float] = None
    cloud_probability_given_cloud: Optional[float] = None


@dataclass(frozen=True)
class DomainSnapshot:
    model_name: str
    forecast_hour: int
    init_time_utc: datetime
    valid_time_utc: datetime
    features: Sequence[GridCellFeature]
    source_url: str
    cache_path: str
    # Surface orography for the snapshot's observer column.  It is kept
    # separate from pressure-level geopotential heights so satellite
    # retrievals cannot accidentally treat a fixed pressure proxy as terrain.
    terrain_elevation_m: Optional[float] = None
    vertical_prior_identity: Optional[str] = None


@dataclass(frozen=True)
class AerosolSample:
    lat: float
    lon: float
    distance_km: float
    bearing_deg: float
    aod550: float


@dataclass(frozen=True)
class AerosolReading:
    aod550: float
    source: str
    quality: str = "fallback"
    observer_lat: Optional[float] = None
    observer_lon: Optional[float] = None
    path_azimuth_deg: Optional[float] = None
    path_samples: List[AerosolSample] = field(default_factory=list)
    pm2_5_ug_m3: Optional[float] = None
    pm10_ug_m3: Optional[float] = None
    particulate_source: Optional[str] = None


@dataclass(frozen=True)
class CloudColumn:
    lat: float
    lon: float
    distance_km: float
    bearing_deg: float
    layers: Mapping[int, GridCellFeature]
    high_presence: float
    mid_presence: float
    low_presence: float
    deep_cloud_risk: float
    thickness_risk: float
    cloud_type: str
    burnability_score: float
    cloud_base_m: float = 0.0
    cloud_top_m: float = 0.0
    liquid_presence: float = 0.0
    ice_presence: float = 0.0
    explicit_cloud_evidence: float = 0.0
    horizontal_cloud_probability: Optional[float] = None
    footprint_azimuth_width_deg: Optional[float] = None
    footprint_near_distance_km: Optional[float] = None
    footprint_far_distance_km: Optional[float] = None


@dataclass(frozen=True)
class CandidateSignal:
    lat: float
    lon: float
    pressure_hpa: int
    distance_km: float
    bearing_deg: float
    sector_delta_deg: float
    apparent_elevation_deg: float
    aerosol_transmission_score: float
    path_clearance_score: float
    illumination_height_m: float
    cloud_height_m: float
    score: float
    rh_percent: float
    cloud_base_m: float = 0.0
    cloud_type: str = "unknown"
    cloud_type_burnability_score: float = 0.5
    deep_cloud_risk: float = 0.0
    aerosol_sensitivity_score: float = 0.0
    aerosol_path_variability_score: float = 0.0
    observer_path_aod550: Optional[float] = None
    inflow_path_aod550: Optional[float] = None
    cloud_evidence_score: float = 0.0
    cloud_fraction_percent: Optional[float] = None
    cloud_liquid_kg_kg: Optional[float] = None
    cloud_ice_kg_kg: Optional[float] = None
    atmospheric_clarity_score: float = 1.0
    illumination_score: float = 0.0
    footprint_azimuth_width_deg: Optional[float] = None
    footprint_near_distance_km: Optional[float] = None
    footprint_far_distance_km: Optional[float] = None
    horizontal_cloud_probability: Optional[float] = None


@dataclass(frozen=True)
class BoundarySignal:
    layer: str
    distance_km: float
    bearing_deg: float
    sector_delta_deg: float
    cloud_base_m: float
    cloud_top_m: float
    required_base_m: float
    apparent_base_elevation_deg: float
    path_clearance_score: float
    aerosol_transmission_score: float
    score: float
    edge_contrast_score: float = 0.0
    clear_slot_score: float = 0.0
    thickness_score: float = 1.0
    cloud_type: str = "unknown"
    cloud_type_burnability_score: float = 0.5
    deep_cloud_risk: float = 0.0
    aerosol_sensitivity_score: float = 0.0
    aerosol_path_variability_score: float = 0.0
    observer_path_aod550: Optional[float] = None
    inflow_path_aod550: Optional[float] = None
    regime: str = "remote_boundary"
    local_cloud_presence_score: float = 0.0
    geometric_reach_km: float = 0.0
    physical_transmission_min: float = 0.0
    physical_transmission_max: float = 0.0
    atmospheric_clarity_score: float = 1.0
    edge_distance_lower_km: Optional[float] = None
    edge_distance_upper_km: Optional[float] = None


@dataclass(frozen=True)
class CloudHoleSignal:
    distance_km: float
    bearing_deg: float
    sector_delta_deg: float
    clear_slot_score: float
    edge_contrast_score: float
    rear_target_distance_km: float
    rear_target_bearing_deg: float
    rear_target_cloud_type: str
    rear_target_burnability_score: float
    rear_target_score: float
    alignment_score: float
    path_clearance_score: float
    rear_blocking_risk: float
    score: float


@dataclass(frozen=True)
class SolarPathProfileLevel:
    pressure_hpa: int
    height_m: float
    rh_percent: float
    temperature_c: Optional[float]
    cloud_fraction_percent: Optional[float]
    cloud_liquid_kg_kg: Optional[float]
    cloud_ice_kg_kg: Optional[float]


@dataclass(frozen=True)
class SolarPathProfileSample:
    distance_km: float
    bearing_deg: float
    cloud_base_m: float
    cloud_top_m: float
    cloud_type: str
    high_presence: float
    mid_presence: float
    low_presence: float
    deep_cloud_risk: float
    thickness_risk: float
    required_illumination_height_m: float
    blocker_risk: float
    lightpath_clearance_score: float
    target_cloud_score: float
    illumination_status: str
    levels: List[SolarPathProfileLevel] = field(default_factory=list)


@dataclass(frozen=True)
class CrossSectionDiagnosis:
    azimuth_deg: float
    samples_count: int
    lightpath_integrity_score: float
    path_extinction_score: float
    vertical_cloud_type: str
    cloud_type_burnability_score: float
    deep_cloud_risk: float
    blocking_risk: float
    target_cloud_score: float
    summary: List[str] = field(default_factory=list)
    profile: List[SolarPathProfileSample] = field(default_factory=list)


@dataclass(frozen=True)
class MapCloudCell:
    lat: float
    lon: float
    distance_km: float
    bearing_deg: float
    high_cloud: float
    mid_cloud: float
    low_cloud: float
    cloud_evidence: float
    cloud_type: str
    cloud_base_m: float
    cloud_top_m: float
    deep_cloud_risk: float
    burn_score: float
    illumination_score: float
    path_clearance_score: float
    aerosol_transmission_score: float
    blocking_risk: float
    atmospheric_clarity_score: float = 1.0


@dataclass(frozen=True)
class SolarInflowBlockage:
    score: float
    distance_km: float
    bearing_deg: float
    pressure_hpa: int
    rh_percent: float
    cloud_base_m: float
    cloud_top_m: float
    ray_height_m: float
    target_distance_km: float
    target_bearing_deg: float
    target_height_m: float


@dataclass(frozen=True)
class TimeStepAssessment:
    forecast_hour: int
    valid_time_utc: datetime
    valid_time_local: datetime
    solar_elevation_deg: float
    solar_azimuth_deg: float
    burn_score: float
    intensity_score: float
    extent_score: float
    vividness_score: float
    visibility_score: float
    low_cloud_penalty: float
    candidate_count: int
    burning_cloud_count: int
    top_candidate: Optional[CandidateSignal]
    strong_burning_cloud_count: int = 0
    top_boundary: Optional[BoundarySignal] = None
    boundary_score: float = 0.0
    boundary_uncertainty: Optional[BoundaryUncertainty] = None
    top_cloud_hole: Optional[CloudHoleSignal] = None
    cloud_hole_score: float = 0.0
    cross_section: Optional[CrossSectionDiagnosis] = None
    solar_inflow_blockage: Optional[SolarInflowBlockage] = None
    model_disagreement_score: float = 0.0
    selected_regime: str = "unknown"
    scene_scores: Dict[str, float] = field(default_factory=dict)
    local_sky_score: float = 0.0
    remote_sky_score: float = 0.0
    convective_risk: float = 0.0
    color_diagnosis: Dict[str, Any] = field(default_factory=dict)
    cloud_source_times_utc: List[datetime] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    map_cells: List[MapCloudCell] = field(default_factory=list)
    geometric_cloud_coverage: float = 0.0
    structure_gate_score: float = 1.0
    # Observation constraints can veto a model-derived target without changing
    # the underlying cloud candidate diagnostics.
    observation_burnable: bool = True
    observation_score_ceiling: Optional[float] = None
    # Calibrated satellite cloud presence can establish that a burnable field
    # contains cloud even when the source does not publish CLWMR/ICMR.  This
    # is retrieval evidence, not a condensate mixing ratio.
    satellite_retrieval_material_evidence: Optional[float] = None
    # Selected by the point scorer's same physical-regime branch that feeds
    # the legacy score. These are diagnostic outputs only; national scoring
    # uses native forward-field loads instead of selected-target AOD proxies.
    selected_observer_path_aod550: Optional[float] = None
    selected_inflow_path_aod550: Optional[float] = None
    selected_aerosol_path_variability_score: float = 0.0
    # These are the authoritative live-scoring channels.  They are required
    # keyword-only fields so old assessments cannot silently fall back to a
    # geometric or candidate-derived value.
    forward_view_cloud_field_amount: float = field(kw_only=True)
    forward_burnable_cloud_field_amount: float = field(kw_only=True)
    observer_local_cloud_field_amount: float = field(kw_only=True)


@dataclass(frozen=True)
class NowcastAdjustment:
    source: str
    image_valid_time_utc: datetime
    image_valid_time_local: datetime
    image_channel: str
    image_area: str
    near_cloud_ratio: float
    far_cloud_ratio: float
    trend_score: float
    visibility_penalty: float
    target_bonus: float
    confidence: float
    summary: List[str] = field(default_factory=list)
    motion_dx_px_per_hour: float = 0.0
    motion_dy_px_per_hour: float = 0.0
    motion_correlation: float = 0.0
    projection_hours: float = 0.0
    # HSD paired-scan provenance and conservative advection diagnostics.
    satellite_source_times_utc: List[datetime] = field(default_factory=list)
    satellite_motion_age_minutes: Optional[float] = None
    satellite_motion_quality: str = "unknown"
    satellite_advection_horizon_hours: float = 0.0
    infrared_near_cloud_ratio: Optional[float] = None
    infrared_far_cloud_ratio: Optional[float] = None
    visible_near_cloud_ratio: Optional[float] = None
    visible_far_cloud_ratio: Optional[float] = None
    channel_fusion_weight_visible: float = 0.0
    local_cloud_ratio: float = 0.0
    horizon_cloud_ratio: float = 0.0
    thin_cloud_ratio: float = 0.0
    thick_cloud_ratio: float = 0.0
    cloud_texture_score: float = 0.0
    cloud_uniformity_score: float = 0.0
    cloud_blanket_score: float = 0.0
    horizon_clearance_score: float = 1.0
    composition_score: float = 0.0
    geometric_path_blocking_risk: float = 0.0
    geometric_path_samples: int = 0
    solar_path_profile: List[SolarPathProfileSample] = field(default_factory=list)
    multispectral_valid_time_utc: Optional[datetime] = None
    spectral_b11_b13_index: Optional[float] = None
    spectral_b13_b15_index: Optional[float] = None
    ice_cloud_probability: Optional[float] = None
    water_cloud_probability: Optional[float] = None
    thin_cirrus_probability: Optional[float] = None
    altocumulus_probability: Optional[float] = None
    altostratus_probability: Optional[float] = None
    cirrus_probability: Optional[float] = None
    cloud_type_label: Optional[str] = None
    cloud_type_confidence: Optional[float] = None
    radar_source: Optional[str] = None
    radar_valid_time_utc: Optional[datetime] = None
    radar_age_minutes: Optional[float] = None
    radar_coverage_ratio: Optional[float] = None
    radar_local_precip_ratio: Optional[float] = None
    radar_path_precip_ratio: Optional[float] = None
    radar_convective_ratio: Optional[float] = None
    radar_max_dbz: Optional[float] = None
    radar_convective_risk: float = 0.0
    radar_reliability_score: float = 0.0
    effective_convective_risk: float = 0.0
    effective_path_blocking_risk: float = 0.0
    convective_score_ceiling: float = 1.0
    airport_station_id: Optional[str] = None
    airport_station_name: Optional[str] = None
    airport_observation_time_utc: Optional[datetime] = None
    airport_distance_km: Optional[float] = None
    # Bearing from the observer to the airport and the airport's site
    # elevation: they gate how much a distant METAR can speak for the light
    # path's air mass (azimuth alignment) and boundary layer (elevation gap).
    airport_bearing_deg: Optional[float] = None
    airport_elevation_m: Optional[float] = None
    airport_cloud_base_m: Optional[float] = None
    airport_cloud_cover: Optional[str] = None
    airport_blocking_risk: float = 0.0
    airport_reliability_score: float = 0.0
    effective_airport_blocking_risk: float = 0.0
    visibility_score_ceiling: float = 1.0
    airport_cavok: Optional[bool] = None
    airport_raw_metar: Optional[str] = None
    observed_features_by_offset: Dict[int, List[GridCellFeature]] = field(kw_only=True)
    observed_feature_times_by_offset: Dict[int, datetime] = field(kw_only=True)
    # Calibrated satellite cloud-column contract.  Display images may still
    # provide motion diagnostics, but they must not populate these fields.
    # The calibrated satellite cloud-column contract is part of every new
    # nowcast payload.  Unknown observations use explicit values below; None
    # would reintroduce the old optional schema.
    # These are keyword-only and required.  A caller must make the new
    # calibrated-observation contract explicit; silently filling an old
    # JPEG-era payload with ``unknown`` would be a compatibility layer.
    nowcast_algorithm: str = field(kw_only=True)
    cloud_column_schema_version: int = field(kw_only=True)
    cloud_column_sets: List[dict] = field(kw_only=True)
    vertical_prior_identity: str = field(kw_only=True)
    vertical_prior_source: str = field(kw_only=True)
    satellite_retrieval_quality: str = field(kw_only=True)
    satellite_low_cloud_state: str = field(kw_only=True)
    satellite_range_bytes: int = field(kw_only=True)
    satellite_range_blocks: int = field(kw_only=True)
    nowcast_point_lat: float = field(kw_only=True)
    nowcast_point_lon: float = field(kw_only=True)
    nowcast_cache_identity: str = field(kw_only=True)
    # A display-only atmospheric section built from calibrated HSD posterior
    # probabilities on the matching GFS pressure levels.  It intentionally
    # keeps unknown cells explicit instead of treating absent cloud evidence
    # as clear sky.
    satellite_vertical_profile: Optional[dict[str, Any]] = field(kw_only=True, default=None)

    def __post_init__(self) -> None:
        if self.nowcast_algorithm != NOWCAST_ALGORITHM_VERSION:
            raise ValueError("nowcast algorithm is obsolete")
        if self.cloud_column_schema_version != NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION:
            raise ValueError("nowcast cloud-column schema is obsolete")
        if not isinstance(self.cloud_column_sets, list):
            raise ValueError("nowcast cloud-column sets must be a list")
        if (
            not isinstance(self.observed_features_by_offset, dict)
            or not isinstance(self.observed_feature_times_by_offset, dict)
            or set(self.observed_features_by_offset) != set(self.observed_feature_times_by_offset)
            or not self.observed_features_by_offset
        ):
            raise ValueError("nowcast must contain calibrated observation offsets")
        for offset_minutes, observed_features in self.observed_features_by_offset.items():
            if (
                isinstance(offset_minutes, bool)
                or not isinstance(offset_minutes, int)
                or offset_minutes < -30
                or offset_minutes > 30
                or offset_minutes % 5 != 0
            ):
                raise ValueError("nowcast observation offset is invalid")
            if not isinstance(observed_features, list) or not all(
                isinstance(feature, GridCellFeature) for feature in observed_features
            ):
                raise ValueError("nowcast observed calibrated features are invalid")
            source_time = self.observed_feature_times_by_offset[offset_minutes]
            if (
                not isinstance(source_time, datetime)
                or source_time.tzinfo is None
                or source_time.utcoffset() != timedelta(0)
            ):
                raise ValueError("nowcast source time must be UTC")
        if not self.cloud_column_sets and (
            self.vertical_prior_identity != "unknown"
            or self.vertical_prior_source != "unknown"
            or self.satellite_retrieval_quality != "unknown"
            or self.satellite_low_cloud_state != "unknown"
        ):
            raise ValueError(
                "an unavailable nowcast must use unknown provenance, quality and low-cloud state"
            )
        if not self.vertical_prior_identity or not self.vertical_prior_source:
            raise ValueError("nowcast vertical prior identity/source are required")
        if not all(math.isfinite(float(value)) for value in (self.nowcast_point_lat, self.nowcast_point_lon)):
            raise ValueError("nowcast point identity is invalid")
        if not -90.0 <= float(self.nowcast_point_lat) <= 90.0:
            raise ValueError("nowcast point latitude is invalid")
        if not -180.0 <= float(self.nowcast_point_lon) <= 180.0:
            raise ValueError("nowcast point longitude is invalid")
        if not self.nowcast_cache_identity:
            raise ValueError("nowcast cache identity is required")
        if self.satellite_retrieval_quality not in {"unknown", "low", "medium", "high"}:
            raise ValueError("nowcast satellite retrieval quality is invalid")
        if self.satellite_low_cloud_state not in {"unknown", "clear", "detected"}:
            raise ValueError("nowcast satellite low-cloud state is invalid")
        if self.satellite_range_bytes < 0 or self.satellite_range_blocks < 0:
            raise ValueError("nowcast satellite range diagnostics are invalid")
        if self.satellite_motion_quality not in {"unknown", "observation", "strong", "decayed", "diagnostic-only"}:
            raise ValueError("nowcast satellite motion quality is invalid")
        if not math.isfinite(float(self.satellite_advection_horizon_hours)) or self.satellite_advection_horizon_hours < 0.0:
            raise ValueError("nowcast satellite advection horizon is invalid")
        if self.satellite_motion_age_minutes is not None and (
            not math.isfinite(float(self.satellite_motion_age_minutes))
            or self.satellite_motion_age_minutes <= 0.0
        ):
            raise ValueError("nowcast satellite motion age is invalid")
        if not isinstance(self.satellite_source_times_utc, list) or any(
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
            for value in self.satellite_source_times_utc
        ):
            raise ValueError("nowcast satellite source times are invalid")
        source_times = [
            value.astimezone(timezone.utc)
            for value in self.satellite_source_times_utc
        ]
        if len(source_times) != len(set(source_times)):
            raise ValueError("nowcast satellite source times must be unique")
        observed_times_in_order = [
            self.observed_feature_times_by_offset[offset].astimezone(timezone.utc)
            for offset in sorted(self.observed_feature_times_by_offset)
        ]
        if any(
            previous >= current
            for previous, current in zip(observed_times_in_order, observed_times_in_order[1:])
        ):
            raise ValueError("nowcast observed source times must be strictly increasing")
        if self.cloud_column_sets:
            from firesky.satellite_cloud_column import (
                aggregate_local_low_cloud_state,
                aggregate_retrieval_quality,
                aggregate_vertical_prior_identity,
                aggregate_vertical_prior_source,
                validate_cloud_column_payload,
            )

            for cloud_set in self.cloud_column_sets:
                if not isinstance(cloud_set, dict):
                    raise ValueError("nowcast cloud-column set must be an object")
                validate_cloud_column_payload(cloud_set)
            # A multi-scan nowcast keeps one flat list for payload size, but
            # every set still belongs to exactly one scored observation time.
            # Validate that relationship here so a set from another scan can
            # never be silently used as evidence for this step.
            observed_times = set()
            for source_time in self.observed_feature_times_by_offset.values():
                observed_times.add(source_time.astimezone(timezone.utc))
            cloud_set_times = set()
            for cloud_set in self.cloud_column_sets:
                try:
                    cloud_time = datetime.fromisoformat(str(cloud_set["valid_time_utc"]))
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("nowcast cloud-column set time is invalid") from exc
                if cloud_time.tzinfo is None or cloud_time.utcoffset() != timedelta(0):
                    raise ValueError("nowcast cloud-column set time must be UTC")
                cloud_set_times.add(cloud_time.astimezone(timezone.utc))
            if cloud_set_times != observed_times:
                raise ValueError(
                    "nowcast cloud-column set times do not match observed offsets"
                )
            expected_identity = aggregate_vertical_prior_identity(self.cloud_column_sets)
            if self.vertical_prior_identity != expected_identity:
                raise ValueError("nowcast vertical prior identity does not match cloud-column sets")
            expected_quality = aggregate_retrieval_quality(
                cloud_set["retrieval_quality"] for cloud_set in self.cloud_column_sets
            )
            if self.satellite_retrieval_quality != expected_quality:
                raise ValueError("nowcast retrieval quality does not match cloud-column sets")
            expected_state = aggregate_local_low_cloud_state(self.cloud_column_sets)
            if self.satellite_low_cloud_state != expected_state:
                raise ValueError("nowcast low-cloud state does not match cloud-column sets")
            expected_source = aggregate_vertical_prior_source(
                column
                for cloud_set in self.cloud_column_sets
                for column in cloud_set["columns"]
            )
            if self.vertical_prior_source != expected_source:
                raise ValueError("nowcast vertical prior source does not match cloud-column sets")


@dataclass(frozen=True)
class ForecastReport:
    observer_name: str
    phenomenon: str
    model_source: str
    target_date: date
    timezone_name: str
    anchor_local: datetime
    cycle_time_utc: datetime
    model_cycles: Dict[str, datetime]
    aerosol: AerosolReading
    overall_score: float
    burn_score: float
    intensity_score: float
    extent_score: float
    vividness_score: float
    duration_minutes: Optional[int]
    confidence_score: float
    best_time_local: Optional[datetime]
    summary: List[str]
    steps: List[TimeStepAssessment]
    forecast_mode: str = "model"
    potential_duration_minutes: Optional[int] = 0
    model_disagreement_score: float = 0.0
    nowcast: Optional[NowcastAdjustment] = None
    calibrated_probability: Optional[float] = None
    calibration_lower_probability: Optional[float] = None
    calibration_upper_probability: Optional[float] = None
    calibration_sample_count: int = 0
    calibration_scope: Optional[str] = None
    calibration_version: Optional[str] = None
    calibration_validation_samples: int = 0
    calibration_raw_brier: Optional[float] = None
    calibration_brier: Optional[float] = None
    calibration_brier_skill: Optional[float] = None
    history_record_id: Optional[int] = None
    model_weights: Dict[str, float] = field(default_factory=dict)
    model_weight_samples: Dict[str, int] = field(default_factory=dict)
    model_component_scores: Dict[str, float] = field(default_factory=dict)
    html_report_path: Optional[str] = None
    compute_backend_requested: str = "auto"
    compute_backend: str = "cpu"
    geometry_window: Optional[GlowGeometryWindow] = None
    boundary_uncertainty: Optional[BoundaryUncertainty] = None
    selected_regime: str = "unknown"
    scene_scores: Dict[str, float] = field(default_factory=dict)
    local_sky_score: float = 0.0
    remote_sky_score: float = 0.0
    convective_risk: float = 0.0
    color_diagnosis: Dict[str, Any] = field(default_factory=dict)
    cloud_evolution: Dict[str, Any] = field(default_factory=dict)
    timing_window: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, list):
                return [convert(item) for item in value]
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            return value

        payload = asdict(self)
        for step in payload.get("steps", []):
            step.pop("map_cells", None)
        nowcast = payload.get("nowcast")
        if isinstance(nowcast, dict):
            nowcast.pop("observed_features_by_offset", None)
            nowcast.pop("observed_feature_times_by_offset", None)
        return convert(payload)
