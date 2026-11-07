// ═══ API Type Definitions ═══
// Mirrors the exact response structures from the Python backend (web_server.py)

// ─── Common ───

export interface ApiError {
  error: string;
}

// ─── Health ───

export interface HealthResponse {
  status: 'ok';
}

// ─── Location ───

export interface LocationResult {
  name: string;
  display_name: string;
  lat: number;
  lon: number;
  source: string;
  timezone_name?: string;
  elevation_m?: number;
}

export interface PointResult {
  lat: number;
  lon: number;
  timezone_name: string;
  elevation_m: number;
}

// ─── Forecast ───

export type Phenomenon = 'dawn' | 'dusk';
export type ModelSource = 'gfs' | 'ec' | 'satellite';
export type SatelliteSource = 'auto' | 'off';
export type SatelliteCloudPhase = 'water' | 'ice' | 'mixed' | 'unknown';
export type SatelliteLowCloudState = 'detected' | 'clear' | 'unknown';
export type SatelliteRetrievalQuality = 'high' | 'medium' | 'low' | 'unknown';

export interface ForecastRequest {
  lat: number;
  lon: number;
  timezone_name: string;
  elevation_m: number;
  target_date: string;           // "YYYY-MM-DD"
  phenomenon: Phenomenon;
  name?: string;
  model_source?: ModelSource;
  satellite_source?: SatelliteSource;
  forecast_mode?: 'model' | 'nowcast';
  gfs_resolution?: '0p25' | '0p50';
  _gfs_cycle_override?: string;
  _ec_cycle_override?: string;
}

export interface PointDataProvenance {
  model_source: ModelSource;
  contract?: string;
  model_product?: string;
  cycle_utc?: string;
  files?: string[];
  gfs_cache_used?: boolean;
  ec_cache_used?: boolean;
  cams_cache_used?: boolean;
  cams_source?: string;
  cams_quality?: string;
  cams_valid_times_utc?: string[];
  cloud_source_fields?: Record<string, string>;
  cloud_boundary_semantics?: string;
  vertical_boundary_source?: string;
  gfs_accessed?: boolean;
  ec_accessed?: boolean;
  observation_used?: boolean;
  [key: string]: unknown;
}

export interface StepPayload {
  valid_time_local: string;      // ISO 8601 local datetime
  valid: boolean;
  solar_elevation_deg: number;
  burn_score: number;
  instant_quality_index?: number;
  instant_quality_level?: string;
  physically_burnable?: boolean;
  observation_burnable?: boolean;
  burnable_cloud_coverage?: number;
  forward_view_cloud_field_amount?: number;
  forward_burnable_cloud_field_amount?: number;
  forward_candidate_cloud_field_amount?: number;
  forward_target_eligible_cloud_field_amount?: number;
  forward_solar_geometry_reachable_cloud_field_amount?: number;
  forward_external_cloud_path_transmitted_field_amount?: number;
  forward_unoccluded_burn_potential_upper_bound?: number;
  forward_burnable_cloud_load?: number;
  forward_raw_burnable_cloud_field_amount?: number;
  solar_incoming_block_risk?: number;
  observer_local_cloud_field_amount?: number;
  observer_local_burnable_cloud_field_amount?: number;
  observer_local_burnable_cloud_load?: number;
  forward_burnable_cloud_ratio?: number;
  forward_high_cloud_amount?: number;
  forward_mid_cloud_amount?: number;
  forward_low_cloud_amount?: number;
  forward_high_burnable_amount?: number;
  forward_mid_burnable_amount?: number;
  forward_low_burnable_amount?: number;
  brightness_color?: number;
  cloud_material_score?: number;
  observer_return_optical_depth?: number | null;
  observer_return_transmission?: number | null;
  observer_local_return_optical_depth?: number | null;
  observer_local_return_transmission?: number | null;
  quality_aod550?: number;
  observer_path_aod550?: number;
  inflow_path_aod550?: number;
  dominant_burnable_pressure_family?: PressureFamily | null;
  dominant_burnable_cloud_base_asl_mean_m?: number | null;
  dominant_burnable_cloud_top_asl_mean_m?: number | null;
  dominant_burnable_target_terrain_asl_mean_m?: number | null;
  dominant_burnable_cloud_base_agl_mean_m?: number | null;
  dominant_burnable_cloud_top_agl_mean_m?: number | null;
  dominant_burnable_cloud_base_above_observer_mean_m?: number | null;
  dominant_burnable_cloud_top_above_observer_mean_m?: number | null;
  dominant_burnable_cloud_phase?: CloudPhaseDiagnosis | null;
  intensity_score: number;
  extent_score: number;
  vividness_score: number;
  visibility_score: number;
  selected_regime?: string;
  scene_scores?: Record<string, number>;
  local_sky_score?: number;
  remote_sky_score?: number;
  convective_risk?: number;
  color_diagnosis?: ColorDiagnosis;
}

export interface AerosolInfo {
  aod550: number;
  source: string;
  quality: string;
  pm2_5_ug_m3?: number | null;
  pm10_ug_m3?: number | null;
  particulate_source?: string | null;
}

export interface CloudOpticsProvenance {
  cloud_optics_source: string;
  effective_radius_source: string;
  cloud_optical_depth_source: string;
}

export interface FireCloudQualityComponents {
  cloud_optics_source?: string;
  effective_radius_source?: string;
  cloud_optical_depth_source?: string;
  physically_burnable_steps?: number;
  observation_burnable_steps?: number;
  observation_burnability_veto_steps?: number;
  candidate_cloud_coverage_peak?: number;
  view_cloud_coverage_peak?: number;
  forward_view_cloud_field_amount_peak?: number;
  forward_view_cloud_field_amount_mean?: number;
  forward_burnable_cloud_field_amount_peak?: number;
  forward_burnable_cloud_field_amount_mean?: number;
  forward_burnable_cloud_field_amount_effective?: number;
  observer_local_cloud_field_amount_peak?: number;
  observer_local_cloud_field_amount_mean?: number;
  observer_local_burnable_cloud_field_amount_peak?: number;
  observer_local_burnable_cloud_field_amount_mean?: number;
  observer_local_burnable_cloud_load_peak?: number;
  observer_local_burnable_cloud_load_mean?: number;
  observer_column_quality_index?: number;
  observer_column_quality_peak?: number;
  observer_column_quality_source?: string;
  forward_burnable_cloud_load_peak?: number;
  forward_burnable_cloud_load_effective?: number;
  forward_raw_burnable_cloud_field_amount_peak?: number;
  forward_raw_burnable_cloud_field_amount_mean?: number;
  forward_burnable_cloud_ratio_peak?: number;
  forward_burnable_cloud_ratio_mean?: number;
  forward_field_radius_km?: number;
  forward_sector_half_angle_deg?: number;
  scoring_scope?: string;
  score_formula?: string;
  score_max?: number;
  burnable_to_view_ratio?: number;
  illumination_area_gate_peak?: number;
  burnable_cloud_coverage_peak?: number;
  burnable_cloud_coverage_mean?: number;
  burnable_cloud_coverage_effective?: number;
  angular_burnable_cloud_coverage_effective?: number;
  quality_coverage_source?: '严格角面积' | '云场支持' | string;
  coverage_base_score?: number;
  coverage_score?: number;
  coverage_score_cap?: number;
  base_quality_index?: number;
  solar_incoming_block_risk_mean?: number;
  solar_incoming_block_risk_peak?: number;
  solar_incoming_block_risk_at_best_cloud?: number;
  duration_minutes?: number;
  duration_factor?: number;
  duration_adjustment?: number;
  duration_score_cap?: number;
  brightness_color?: number;
  brightness_adjustment?: number;
  cloud_material?: number;
  cloud_material_adjustment?: number;
  cloud_base?: number;
  cloud_base_adjustment?: number;
  clearance_adjustment?: number;
  clearance_score_cap?: number;
  observer_path_aod550?: number;
  inflow_path_aod550?: number;
  quality_aod550?: number;
  aerosol_path_variability?: number;
  local_aod550?: number;
  incoming_aod_illumination_factor?: number;
  observer_aod_contrast_factor?: number;
  observer_return_optical_depth_mean?: number;
  observer_return_transmission_mean?: number;
  incoming_aod_adjustment?: number;
  observer_aod_adjustment?: number;
  effective_cloud_base_m?: number;
  aerosol_equivalent_ground_height_m?: number;
  geometric_max_distance_km?: number;
  raw_quality_index?: number;
  quality_score_cap?: number;
  quality_limiting_factor?: '无可烧云' | '可烧云绝对量' | '可烧云比例' | '远处厚云墙' | '远处低中云墙' | '云量' | '照明' | '垂直结构' | '时长' | 'AOD' | '通视' | string;
}

export interface ForecastPayload {
  observer_name: string;
  phenomenon: Phenomenon;
  model_source: ModelSource;
  target_date: string;
  analysis_radius_km?: number;
  timezone_name: string;
  anchor_local: string;
  cycle_time_utc: string;
  algorithm?: string;
  model_cycles: Record<string, string>;
  best_time_local: string | null;
  overall_score: number;
  intensity_score: number;
  extent_score: number;
  vividness_score: number;
  quality_index?: number;
  quality_level?: string;
  peak_quality_index?: number;
  peak_quality_level?: string;
  quality_components?: FireCloudQualityComponents;
  cloud_optics?: CloudOpticsProvenance;
  dominant_burnable_pressure_family?: PressureFamily | null;
  dominant_burnable_cloud_base_asl_mean_m?: number | null;
  dominant_burnable_cloud_top_asl_mean_m?: number | null;
  dominant_burnable_target_terrain_asl_mean_m?: number | null;
  dominant_burnable_cloud_base_agl_mean_m?: number | null;
  dominant_burnable_cloud_top_agl_mean_m?: number | null;
  dominant_burnable_cloud_base_above_observer_mean_m?: number | null;
  dominant_burnable_cloud_top_above_observer_mean_m?: number | null;
  dominant_burnable_cloud_phase?: CloudPhaseDiagnosis | null;
  dominant_burnable_cloud_genus_diagnosis?: CloudGenusDiagnosis | null;
  cloud_genus_proxy?: CloudGenusProxyDiagnosis | null;

  confidence_score: number;
  model_disagreement_score: number;
  duration_minutes: number;
  potential_duration_minutes: number;
  model_weights: Record<string, number>;
  geometry_window?: GlowGeometryWindow | null;
  boundary_uncertainty?: BoundaryUncertainty | null;
  selected_regime?: string;
  scene_scores?: Record<string, number>;
  local_sky_score?: number;
  remote_sky_score?: number;
  convective_risk?: number;
  color_diagnosis?: ColorDiagnosis;
  cloud_evolution?: CloudEvolution;
  timing_window?: ForecastTimingWindow | null;
  aerosol: AerosolInfo;
  summary: string[];
  steps: StepPayload[];
  html_report_path: string | null;
  map_token: string | null;
  map_times: string[];
  map_capability?: {
    available: boolean;
    model_source?: 'gfs' | 'ec';
    region?: string;
    source?: 'existing-regional-cache';
    reason?: string;
    regional_cache_candidates?: number;
  };
  html_report_url: string;
  forecast_mode?: 'model' | 'nowcast';
  compute_backend_requested?: 'auto' | 'cpu' | 'gpu';
  compute_backend?: 'cpu' | 'gpu' | 'hybrid' | 'cache';
  nowcast?: NowcastInfo | null;
  observer_elevation_asl_m: number;
  /** Strict same-point GFS cycle revisions embedded in this result. */
  cross_section?: NationalCrossSectionPayload | null;
  cross_section_request?: NationalCrossSectionRequest | null;
  cross_section_capability?: {
    available: boolean;
    model_source?: 'gfs' | 'ec';
    reason?: string | null;
  };
  diagnostic_source?: string;
  point_grid_resolution?: PointGridResolution;
  source_roles?: {
    primary?: string;
    auxiliary?: string;
  };
  data_provenance?: PointDataProvenance;
  ecmwf_provenance?: PointDataProvenance;
}

export interface PointGridResolution {
  mode: 'nearest-regional-grid-v1';
  region: string;
  grid_spacing_deg: number;
  requested_lat: number;
  requested_lon: number;
  resolved_lat: number;
  resolved_lon: number;
  resolved_timezone_name: string;
  resolved_elevation_asl_m?: number;
}

export interface GlowGeometryWindow {
  regime: string;
  start_time_utc: string;
  start_time_local: string;
  midpoint_time_utc: string;
  midpoint_time_local: string;
  end_time_utc: string;
  end_time_local: string;
  duration_minutes: number;
  shadow_speed_km_per_min: number;
  edge_distance_km: number;
  cloud_base_m: number;
  geometric_max_distance_km: number;
}

export interface BoundaryUncertainty {
  regime: string;
  source: string;
  nominal_distance_km: number;
  lower_distance_km: number;
  upper_distance_km: number;
  width_km: number;
  score_min: number;
  score_max: number;
  event_threshold: number;
  classification_stable: boolean;
  nearest_flip_displacement_km: number | null;
  start_time_earliest_local: string | null;
  start_time_latest_local: string | null;
  midpoint_time_earliest_local: string | null;
  midpoint_time_latest_local: string | null;
  end_time_earliest_local: string | null;
  end_time_latest_local: string | null;
  duration_min_minutes: number | null;
  duration_max_minutes: number | null;
}

export interface ForecastTimingWindow {
  regime: string;
  source: 'earth-shadow-geometry' | 'score-curve-interpolated';
  threshold?: number;
  start_time_local: string;
  peak_time_local: string;
  end_time_local: string;
  duration_minutes: number;
}

export type PressureFamily = 'high' | 'middle' | 'low';

export interface CloudPhaseDiagnosis {
  label: 'ice' | 'mixed' | 'water' | 'unknown' | string;
  ice_fraction: number | null;
  confidence: number;
  source?: string;
}

export interface CloudGenusDiagnosis {
  label: 'unknown';
  status: 'compatible-only' | 'unknown';
  confidence: number;
  scoring_effect: false;
  compatible_genera: string[];
  reason: string;
  source: string;
  model_source: string;
  evidence: {
    dominant_pressure_family: PressureFamily;
    cloud_phase: string | null;
    cloud_base_agl_m: number | null;
    cloud_top_agl_m: number | null;
    convective_risk: number;
  };
}

export interface CloudGenusProxyDiagnosis {
  label: string;
  status: 'proxy';
  confidence: number;
  scoring_effect: false;
  candidate_scores: Record<string, number>;
  compatible_genera: string[];
  source: string;
  model_source: string;
  evidence: {
    pressure_family: PressureFamily;
    cloud_phase: string | null;
    cloud_base_agl_m: number | null;
    cloud_top_agl_m: number | null;
    convective_risk: number;
  };
}

export interface ColorDiagnosis {
  type: 'golden' | 'orange_red' | 'deep_red' | 'muted_glow' | string;
  confidence: number;
  cloud_phase?: string;
  source?: string;
  inputs?: {
    quality_strength?: number;
    brightness_color?: number;
    solar_elevation_deg?: number;
    aod550?: number;
    solar_incoming_illumination_score?: number;
    deep_cloud_risk?: number;
    cloud_phase?: string;
  };
  phase?: CloudPhaseDiagnosis | null;
}

export interface CloudEvolution {
  trend: 'forming' | 'strengthening' | 'steady' | 'weakening' | 'dissipating' | 'unknown' | string;
  target_score_change: number;
  cloud_hole_closure_risk: number;
  source: string;
  comparison_start_local?: string;
  comparison_end_local?: string;
  comparison_minutes?: number;
}

export interface SatelliteCloudColumn {
  schema_version: 4;
  algorithm: string;
  valid_time_utc: string;
  lat: number;
  lon: number;
  distance_km: number;
  bearing_deg: number;
  base_p10_asl_m: number | null;
  base_p50_asl_m: number | null;
  base_p90_asl_m: number | null;
  base_p10_agl_m: number | null;
  base_p50_agl_m: number | null;
  base_p90_agl_m: number | null;
  top_asl_m: number | null;
  top_agl_m: number | null;
  terrain_asl_m: number | null;
  phase: SatelliteCloudPhase;
  cloud_type: string;
  optical_depth: number | null;
  cloud_probability_by_height: Record<string, number>;
  horizontal_cloud_probability: number | null;
  low_cloud_state: SatelliteLowCloudState;
  retrieval_quality: SatelliteRetrievalQuality;
  source: string;
  vertical_prior_identity: string;
  vertical_prior_source: string;
  base_source: string;
  motion_dx_px_per_hour: number;
  motion_dy_px_per_hour: number;
}

export interface SatelliteCloudColumnSet {
  schema_version: 4;
  valid_time_utc: string;
  source: string;
  vertical_prior_identity: string;
  retrieval_quality: SatelliteRetrievalQuality;
  low_cloud_state: SatelliteLowCloudState;
  motion_dx_px_per_hour: number;
  motion_dy_px_per_hour: number;
  columns: SatelliteCloudColumn[];
}

export interface NowcastInfo {
  nowcast_algorithm: string;
  source: string;
  image_valid_time_utc: string;
  image_channel: string;
  image_area: string;
  near_cloud_ratio: number;
  far_cloud_ratio: number;
  trend_score: number;
  confidence: number;
  motion_dx_px_per_hour?: number;
  motion_dy_px_per_hour?: number;
  motion_correlation?: number;
  projection_hours: number;
  satellite_source_times_utc: string[];
  satellite_motion_age_minutes?: number | null;
  satellite_motion_quality: 'unknown' | 'observation' | 'strong' | 'decayed' | 'diagnostic-only';
  satellite_advection_horizon_hours: number;
  infrared_near_cloud_ratio?: number | null;
  infrared_far_cloud_ratio?: number | null;
  visible_near_cloud_ratio?: number | null;
  visible_far_cloud_ratio?: number | null;
  channel_fusion_weight_visible?: number;
  local_cloud_ratio?: number;
  horizon_cloud_ratio?: number;
  thin_cloud_ratio?: number;
  thick_cloud_ratio?: number;
  cloud_texture_score?: number;
  cloud_uniformity_score?: number;
  cloud_blanket_score?: number;
  horizon_clearance_score?: number;
  geometric_path_blocking_risk?: number;
  geometric_path_samples?: number;
  solar_path_profile?: SolarPathProfileSample[];
  composition_score?: number;
  multispectral_valid_time_utc?: string | null;
  spectral_b11_b13_index?: number | null;
  spectral_b13_b15_index?: number | null;
  ice_cloud_probability?: number | null;
  water_cloud_probability?: number | null;
  thin_cirrus_probability?: number | null;
  altocumulus_probability?: number | null;
  altostratus_probability?: number | null;
  cirrus_probability?: number | null;
  cloud_type_label?: string | null;
  cloud_type_confidence?: number | null;
  radar_source?: string | null;
  radar_valid_time_utc?: string | null;
  radar_age_minutes?: number | null;
  radar_coverage_ratio?: number | null;
  radar_local_precip_ratio?: number | null;
  radar_path_precip_ratio?: number | null;
  radar_convective_ratio?: number | null;
  radar_max_dbz?: number | null;
  radar_convective_risk?: number;
  radar_reliability_score?: number;
  effective_convective_risk?: number;
  effective_path_blocking_risk?: number;
  convective_score_ceiling?: number;
  calibrated_sample_coverage_ratio?: number;
  cloud_column_schema_version: 4;
  cloud_column_sets: SatelliteCloudColumnSet[];
  vertical_prior_identity: string;
  vertical_prior_source: string;
  satellite_retrieval_quality: SatelliteRetrievalQuality;
  satellite_low_cloud_state: SatelliteLowCloudState;
  satellite_range_bytes: number;
  satellite_range_blocks: number;
  nowcast_point_lat: number;
  nowcast_point_lon: number;
  nowcast_cache_identity: string;
  satellite_vertical_profile?: SatelliteVerticalProfile | null;
}

export interface SatelliteVerticalProfileLevel {
  pressure_hpa: number;
  height_m: number | null;
  rh_percent: number | null;
  temperature_c: number | null;
  model_cloud_fraction: number | null;
  model_cloud_fraction_percent?: number | null;
  hsd_posterior: number | null;
  hsd_posterior_percent?: number | null;
  cloud_probability_percent: number | null;
  horizontal_cloud_probability: number | null;
  status: 'retrieved' | 'unknown';
}

export interface SatelliteVerticalProfileSample {
  distance_km: number;
  bearing_deg: number;
  status: 'retrieved' | 'unknown';
  retrieval_quality: SatelliteRetrievalQuality;
  vertical_prior_identity: string;
  vertical_prior_source: string;
  levels: SatelliteVerticalProfileLevel[];
}

export interface SatelliteVerticalProfile {
  schema_version: 1;
  status: 'available' | 'partial_unknown' | 'unavailable';
  source: string;
  valid_time_utc: string;
  bands: string[]; // HSD B07/B13/B15 or complete HCAI CMSK/CPHS/CHGT fields
  prior_source: 'gfs';
  sample_count: number;
  usable_sample_count: number;
  unknown_sample_count: number;
  coverage: number;
  numeric_clear_sky_allowed: false;
  pressure_levels_hpa: number[];
  samples: SatelliteVerticalProfileSample[];
}

export interface SolarPathProfileLevel {
  pressure_hpa: number;
  height_m: number;
  rh_percent: number;
  temperature_c: number | null;
  cloud_fraction_percent: number | null;
  cloud_liquid_kg_kg: number | null;
  cloud_ice_kg_kg: number | null;
}

export interface SolarPathProfileSample {
  distance_km: number;
  bearing_deg: number;
  cloud_base_m: number;
  cloud_top_m: number;
  cloud_type: string;
  high_presence: number;
  mid_presence: number;
  low_presence: number;
  deep_cloud_risk: number;
  thickness_risk: number;
  required_illumination_height_m: number;
  blocker_risk: number;
  lightpath_clearance_score: number;
  target_cloud_score: number;
  illumination_status: string;
  levels: SolarPathProfileLevel[];
}

// ─── Async Forecast (SSE) ───

export interface ForecastStartResponse {
  job_id: string;
}

export type RegionalForecastArea =
  | 'china'
  | 'east-asia'
  | 'southeast-asia'
  | 'south-asia'
  | 'europe'
  | 'north-america'
  | 'south-america'
  | 'oceania'
  | 'africa-middle-east'
  | 'north-asia'
  | 'custom';

export interface RegionalBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface RegionalForecastRequest {
  target_date: string;
  phenomenon: Phenomenon;
  model_source: 'gfs' | 'ec';
  forecast_mode?: 'model' | 'nowcast';
  satellite_source?: SatelliteSource;
  spacing_deg?: number;
  workers?: number;
  model_cadence_hours?: 1 | 3;
  compute_backend?: 'auto' | 'cpu' | 'gpu';
  gfs_resolution?: '0p25' | '0p50';
  _gfs_cycle_override?: string;
  _ec_cycle_override?: string;
  regions: RegionalForecastArea[];
  custom_bounds?: RegionalBounds;
  custom_label?: string;
  custom_timezone_name?: string;
  profile_key?: string;
  elevation_m?: number;
}

export interface NationalCrossSectionRequest extends RegionalForecastRequest {
  elevation_m: number;
}

export interface RegionalForecastPoint {
  name: string;
  lat: number;
  lon: number;
  observer_elevation_asl_m: number;
  quality_index: number;
  quality_level: string;
  peak_quality_index: number;
  peak_quality_level: string;
  dominant_burnable_pressure_family: PressureFamily | null;
  confidence_score: number;
  best_time_local: string | null;
  burnable_cloud_coverage_effective: number;
  forward_view_cloud_field_amount_peak: number;
  forward_burnable_cloud_field_amount_peak: number;
  forward_burnable_cloud_ratio_peak: number;
  burnable_duration_minutes: number;
  local_aod550: number;
  quality_aod550: number;
  incoming_aod_illumination_factor: number;
  observer_aod_contrast_factor: number;
  observer_return_optical_depth_mean: number;
  observer_return_transmission_mean: number;
  incoming_aod_adjustment: number;
  observer_aod_adjustment: number;
  high_cloud: number;
  mid_cloud: number;
  low_cloud: number;
}

export interface RegionalForecastFrame {
  selected_column_diagnostic_source: 'gfs-selected-column-bilinear-diagnostic';
  offset_minutes: number;
  valid: string;
  instant_quality_index: string;
  physically_burnable: string;
  burnable_cloud_coverage: string;
  forward_view_cloud_field_amount: string;
  forward_burnable_cloud_field_amount: string;
  forward_candidate_cloud_field_amount: string;
  forward_target_eligible_cloud_field_amount: string;
  forward_solar_geometry_reachable_cloud_field_amount: string;
  forward_external_cloud_path_transmitted_field_amount: string;
  forward_unoccluded_burn_potential_upper_bound: string;
  forward_burnable_cloud_load: string;
  forward_burnable_cloud_material_load: string;
  forward_burnable_cloud_incoming_aerosol_load: string;
  solar_incoming_block_risk: string;
  observer_local_cloud_field_amount: string;
  observer_local_burnable_cloud_field_amount: string;
  observer_local_burnable_cloud_load: string;
  observer_local_burnable_cloud_material_load: string;
  observer_local_burnable_cloud_incoming_aerosol_load: string;
  forward_burnable_cloud_ratio: string;
  burn_score: string;
  intensity_score: string;
  extent_score: string;
  vividness_score: string;
  high_cloud: string;
  mid_cloud: string;
  low_cloud: string;
  high_burn: string;
  mid_burn: string;
  low_burn: string;
  cloud_evidence: string;
  selected_column_cloud_material_class_code: string;
  convective_risk: string;
  dominant_burnable_pressure_family_code: string;
  illumination_score: string;
  path_clearance_score: string;
  blocking_risk: string;
  target_score: string;
  dominant_burnable_cloud_base_asl_mean_m: string;
  dominant_burnable_cloud_top_asl_mean_m: string;
  dominant_burnable_target_terrain_asl_mean_m: string;
  local_aod550: string;
  target_deck_connected: string;
  target_deck_attenuated_fraction: string;
  target_deep_risk: string;
  cloud_optical_depth: string;
  peak_blocking: string;
  clear_gap_count: string;
  observer_return_optical_depth: string;
  observer_return_transmission: string;
  observer_local_return_optical_depth: string;
  observer_local_return_transmission: string;
}

export interface RegionalForecastFrameData {
  version: 102;
  encoding: 'base64-quantized-row-major-v1';
  selected_column_diagnostic_source: 'gfs-selected-column-bilinear-diagnostic';
  point_count: number;
  height_scale_m: number;
  cloud_optics: CloudOpticsProvenance;
  precision: RegionalForecastPrecision;
  performance: {
    instant_scoring_seconds: number;
    assembly_seconds: number;
    total_seconds: number;
    points_per_second: number;
    samples_per_second: number;
  };
  frames: RegionalForecastFrame[];
}

export interface RegionalForecastPrecision {
  mode: string;
  point_spacing_deg: number | null;
  model_grid_spacing_deg: number;
  point_count: number;
  processed_sample_count: number;
  candidate_limit_per_sample: number;
  vertical_profiles: boolean;
  full_point_coverage: boolean;
  aerosol_profile_mode: string;
}

export interface RegionalForecastCoverage {
  code: RegionalForecastArea;
  label: string;
  bounds: [number, number, number, number];
  timezone_name: string | null;
  point_prefix: string;
}

export interface RegionalGfsDataProvenance {
  cache_status: 'refresh' | 'hit' | 'stale';
  cams_source: 'cams' | null;
  generated_at_utc: string;
  stale: boolean;
  inputs_complete: boolean;
  requested_gfs_cycle_utc: string;
  served_gfs_cycle_utc: string;
  gfs_forecast_hours: number[];
  gfs_valid_times_utc: string[];
  cams_run_times_utc: string[];
  cams_valid_times_utc: string[];
  algorithm: string;
}

export interface RegionalExternalDataProvenance {
  model_source: 'ec';
  cycle_utc: string;
  forecast_hours: number[];
  model_files: string[];
  cams_files: string[];
}

export type RegionalDataProvenance = RegionalGfsDataProvenance | RegionalExternalDataProvenance;

interface RegionalForecastPayloadBase {
  scope: 'regional';
  point_mode: 'explicit-single-points' | 'generated-regional-grid';
  regions: RegionalForecastArea[];
  coverage: RegionalForecastCoverage[];
  target_date: string;
  phenomenon: Phenomenon;
  algorithm: string;
  cycle_time_utc: string;
  precision: RegionalForecastPrecision;
  timing: RegionalForecastTiming;
  point_count: number;
  success_count: number;
  error_count: number;
  point_format: 'array-v6';
  point_fields: string[];
  points: RegionalForecastPoint[];
  errors: Array<{ name: string; lat: number; lon: number; error: string }>;
}

export interface RegionalForecastTiming {
    prefetch_seconds: number;
    tensor_seconds: number;
    compute_seconds: number;
    frames_seconds: number;
    frame_instant_scoring_seconds: number;
    frame_assembly_seconds: number;
    frame_points_per_second: number;
    frame_samples_per_second: number;
    total_seconds: number;
}

export interface RegionalForecastCache {
  status: 'refresh' | 'hit' | 'stale';
  key: string;
  generated_at_utc: string;
  version: number;
}

export interface RegionalGfsForecastPayload extends RegionalForecastPayloadBase {
  version: 53;
  model_source: 'gfs';
  forecast_mode: 'model' | 'nowcast';
  compute_backend_requested: 'auto' | 'cpu' | 'gpu';
  compute_backend: 'cpu' | 'gpu' | 'hybrid';
  cache_recommendation: string;
  data_provenance: RegionalGfsDataProvenance;
  cache: RegionalForecastCache;
}

export interface RegionalExternalForecastPayload extends RegionalForecastPayloadBase {
  version: 1;
  model_source: 'ec';
  model_resolution: '0p25';
  forecast_hours: number[];
  data_provenance: RegionalExternalDataProvenance;
  cache: RegionalForecastCache;
}

export type RegionalForecastPayload = RegionalGfsForecastPayload | RegionalExternalForecastPayload;

export interface NationalCrossSectionFields {
  geopotential_height_asl_m: Array<Array<number | null>>;
  temperature_c: Array<Array<number | null>>;
  rh_percent: Array<Array<number | null>>;
  cloud_fraction_percent: Array<Array<number | null>>;
  cloud_presence: Array<Array<number | null>>;
  cloud_amount: Array<Array<number | null>>;
  cloud_liquid_kg_kg: Array<Array<number | null>>;
  cloud_ice_kg_kg: Array<Array<number | null>>;
  omega_pa_s: Array<Array<number | null>>;
  u_wind_ms: Array<Array<number | null>>;
  v_wind_ms: Array<Array<number | null>>;
  /** 1: non-target cloud, 2: lit but below horizon, 3: theoretically burnable and visible. */
  cloud_layer_status: Array<Array<number | null>>;
  pressure_family_code: Array<Array<number>>;
  cloud_layer_base_asl_m: Array<Array<number | null>>;
  cloud_layer_top_asl_m: Array<Array<number | null>>;
}

export interface NationalCrossSectionPayload {
  model_source?: 'gfs' | 'ec' | null;
  terrain_source?: string;
  spatial_sampling?: string;
  observer: { lat: number; lon: number; elevation_asl_m: number };
  offset_minutes: number;
  valid_time_local: string;
  solar_elevation_deg: number;
  solar_azimuth_deg: number;
  analysis_radius_km: number;
  distances_km: number[];
  terrain_elevation_asl_m: number[];
  pressure_levels_hpa: number[];
  required_illumination_base_asl_m: number[];
  required_illumination_base_agl_m: number[];
  theoretical_targets: Array<{
    distance_km: number;
    pressure_family: PressureFamily;
    pressure_family_label: string;
    cloud_phase: CloudPhaseDiagnosis;
    cloud_base_asl_m: number;
    cloud_top_asl_m: number;
    target_terrain_asl_m: number;
    cloud_base_agl_m: number;
    cloud_top_agl_m: number;
    cloud_base_above_observer_m: number;
    cloud_top_above_observer_m: number;
    geometric_horizon_margin_m: number;
    observer_terrain_blocked: boolean;
    observer_terrain_clearance_m: number | null;
    required_base_asl_m: number;
    required_base_agl_m: number;
    status: 'burnable' | 'illuminated_not_visible' | 'not_burnable';
    status_reason: string;
  }>;
  fields: NationalCrossSectionFields;
}

export interface RegionalForecastDiagnosticResponse {
  lat: number;
  lon: number;
  observer_name: string;
  diagnostic: Record<string, unknown>;
}

export interface RegionalForecastStartResponse extends ForecastStartResponse {
  scope: 'regional';
}

export type SSEStage =
  | 'queued' | 'validate' | 'service' | 'cache'
  | 'calibration' | 'history' | 'report' | 'map_frames'
  | 'national' | 'batch_compute' | 'target_path' | 'frames'
  | 'finalize' | 'connected' | 'fallback' | 'done' | 'failed';

export interface SSEProgressEvent {
  event: 'progress';
  stage: SSEStage;
  percent: number;
  message: string;
  elapsed_seconds: number;
  details?: Record<string, unknown>;
}

export interface SSEDoneEvent {
  event: 'done';
  stage: 'done';
  percent: 100;
  message: string;
  elapsed_seconds: number;
  result?: ForecastPayload;
  result_id?: string;
}

export interface SSEFailedEvent {
  event: 'failed';
  stage: 'failed';
  percent: 100;
  message: string;
  elapsed_seconds: number;
}

export type SSEForecastEvent = SSEProgressEvent | SSEDoneEvent | SSEFailedEvent;

// ─── Map Frame ───

export interface MapCloudCell {
  lat: number;
  lon: number;
  distance_km: number;
  bearing_deg: number;
  high_cloud: number;
  mid_cloud: number;
  low_cloud: number;
  cloud_evidence: number;
  deep_cloud_risk: number;
  burn_score: number;
  illumination_score: number;
  path_clearance_score: number;
  blocking_risk: number;
}

export interface MapFrameObserver {
  lat: number;
  lon: number;
}

export interface MapFramePayload {
  valid_time_local: string;
  valid: boolean;
  solar_elevation_deg: number;
  solar_azimuth_deg: number;
  instant_quality_index: number;
  physically_burnable: boolean;
  burnable_cloud_coverage: number;
  forward_view_cloud_field_amount: number;
  forward_burnable_cloud_field_amount: number;
  forward_burnable_cloud_load: number;
  forward_burnable_cloud_material_load: number;
  forward_burnable_cloud_incoming_aerosol_load: number;
  solar_incoming_block_risk: number;
  forward_burnable_cloud_ratio: number;
  observer: MapFrameObserver;
  cells: MapCloudCell[];
}

// ─── Feedback ───

export interface FeedbackPredictedStep {
  valid_time_local: string;
  intensity_score: number;
  extent_score: number;
  vividness_score: number;
}

export interface FeedbackObservationStep {
  valid_time_local: string;
  intensity_score: number | null;
  extent_score: number | null;
  vividness_score: number | null;
}

export interface FeedbackItem {
  id: number;
  target_date: string;
  observer_name: string;
  lat: number;
  lon: number;
  phenomenon: Phenomenon;
  model_source: ModelSource;
  raw_score: number;
  confidence: number;
  predicted_extent: number;
  predicted_vividness: number;
  predicted_intensity: number;
  best_time_local: string | null;
  observed: boolean | null;
  extent_score: number | null;
  vividness_score: number | null;
  notes: string | null;
  image_path: string | null;
  label_source: string | null;
  start_time_local: string | null;
  peak_time_local: string | null;
  end_time_local: string | null;
  peak_intensity_score: number | null;
  model_sources: ModelSource[];
  summary: string[];
  predicted_steps: FeedbackPredictedStep[];
  observation_steps: FeedbackObservationStep[];
}

export interface FeedbackListResponse {
  items: FeedbackItem[];
}

export interface FeedbackSubmitRequest {
  forecast_id: number;
  observed: boolean;
  extent_score?: number | null;
  vividness_score?: number | null;
  notes?: string | null;
  image_path?: string | null;
  label_source?: string | null;
  start_time_local?: string | null;
  peak_time_local?: string | null;
  end_time_local?: string | null;
  peak_intensity_score?: number | null;
  steps?: {
    valid_time_local: string;
    intensity_score?: number | null;
    extent_score?: number | null;
    vividness_score?: number | null;
  }[];
}

export interface FeedbackSubmitResponse {
  status: 'ok';
  forecast_id: number;
}

// ─── Satellite ───

export interface SatelliteArea {
  code: string;
  bounds: [number, number, number, number]; // [W, E, N, S]
}

export interface SatelliteFrameInfo {
  token: string;
  time: string;          // ISO 8601 UTC
  age_min: number;
}

export interface SatelliteFramesResponse {
  area: SatelliteArea;
  channel: 'b03' | 'b13';
  frames: SatelliteFrameInfo[];
}

export interface SatelliteMotionResponse {
  dx_pixels: number;
  dy_pixels: number;
  correlation: number;
  interval_minutes: number;
}

// ─── Auth ───

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: string;
  status: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface RegisterRequest {
  email: string;
  password: string;
  display_name?: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface ChangePasswordRequest {
  old_password: string;
  new_password: string;
}

export interface UserListResponse {
  users: User[];
  total: number;
}

export interface UpdateUserStatusRequest {
  status: 'approved' | 'rejected';
}

export interface UpdateUserRoleRequest {
  role: 'user' | 'admin';
}

export interface CreateUserRequest {
  email: string;
  password: string;
  display_name?: string;
  role?: 'user' | 'admin';
  status?: 'approved' | 'pending' | 'rejected';
}

export interface UpdateUserRequest {
  email?: string;
  display_name?: string;
  password?: string;
  role?: 'user' | 'admin';
  status?: 'approved' | 'pending' | 'rejected';
}

// ─── Tasks & Schedules ───

export type TaskType = 'shell' | 'http';
export type TaskRunStatus = 'running' | 'success' | 'failed';

export interface Task {
  id: string;
  name: string;
  description: string;
  task_type: TaskType;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface TaskSchedule {
  id: string;
  task_id: string;
  name: string;
  description: string;
  cron_expr: string;
  enabled: boolean;
  last_run_at: string | null;
  last_run_status: TaskRunStatus | null;
  last_run_output: string | null;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
  task_name?: string;
  task_type?: TaskType;
}

export interface TaskRun {
  id: number;
  schedule_id: string;
  started_at: string;
  finished_at: string | null;
  status: TaskRunStatus;
  output: string | null;
  error: string | null;
}

export interface TaskListResponse {
  tasks: Task[];
  total: number;
}

export interface ScheduleListResponse {
  schedules: TaskSchedule[];
  total: number;
}

export interface TaskRunListResponse {
  runs: TaskRun[];
  total: number;
}

export interface CreateTaskRequest {
  name: string;
  description?: string;
  task_type: TaskType;
  config?: Record<string, unknown>;
}

export interface UpdateTaskRequest {
  name?: string;
  description?: string;
  task_type?: TaskType;
  config?: Record<string, unknown>;
}

export interface CreateScheduleRequest {
  task_id: string;
  name: string;
  description?: string;
  cron_expr: string;
  enabled?: boolean;
}

export interface UpdateScheduleRequest {
  name?: string;
  description?: string;
  cron_expr?: string;
  enabled?: boolean;
}

// ─── Forecast Jobs (Go dispatcher queue) ───

// Status values exactly as the firesky backend's ForecastJob reports them.
export type ForecastJobStatus =
  | 'running'
  | 'done'
  | 'failed'
  | 'cancelled';

export interface ForecastJob {
  id: string;
  cache_key: string;
  request_payload: string;
  algorithm_version: string;
  data_version: string;
  status: ForecastJobStatus;
  progress_stage: string | null;
  progress_percent: number;
  progress_message: string | null;
  error_message: string | null;
  result_id: string | null;
  created_by: string | null;
  force_refresh: boolean;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ForecastJobListResponse {
  items: ForecastJob[];
  total: number;
  offset: number;
  limit: number;
}

// ─── User Cities ───

export interface UserCity {
  id: string;
  user_id: string;
  name: string;
  lat: number;
  lon: number;
  timezone_name: string;
  elevation_m: number;
  sort_order: number;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateCityRequest {
  name: string;
  lat: number;
  lon: number;
  timezone_name?: string;
  elevation_m?: number;
  is_default?: boolean;
}

export interface UpdateCityRequest {
  name?: string;
  sort_order?: number;
  is_default?: boolean;
  timezone_name?: string;
  elevation_m?: number;
}

export interface ReorderCitiesRequest {
  order: string[];
}

export interface CityListResponse {
  cities: UserCity[];
  total: number;
}

/** EC/GFS 单点共有的溯源与语义字段（v0.1.1 对齐）。 */
export interface PointPayloadAlignment {
  confidence_score_semantics?: string;
  data_provenance?: {
    model_source: string;
    cycle_utc: string;
    gfs_cache_used: boolean;
    cams_cache_used: boolean;
    observation_used: boolean;
  };
  duration_status?: 'estimated' | 'unknown' | string;
  point_refinement?: string;
  diagnostic_source?: string;
  map_capability?: { available: boolean; model_source?: string; reason?: string };
  ecmwf_provenance?: Record<string, unknown>;
  point_grid_resolution?: Record<string, unknown>;
}
