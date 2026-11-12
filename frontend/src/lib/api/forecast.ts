import { get, post } from './client';
import {
  requireStrictIsoDate,
  requireStrictUtcIsoTime,
  requireStrictZonedIsoTime,
} from './date-contract';
import { requireProductionChinaSummary } from './regional-summary-contract';
import type {
  ForecastPayload,
  ForecastRequest,
  ForecastStartResponse,
  NowcastInfo,
  RegionalBounds,
  RegionalForecastPayload,
  RegionalForecastPoint,
  RegionalForecastRequest,
  RegionalForecastStartResponse,
  RegionalForecastDiagnosticResponse,
  NationalCrossSectionPayload,
  NationalCrossSectionRequest,
} from './types';

const COMPACT_POINT_FIELDS = [
  'name',
  'lat',
  'lon',
  'observer_elevation_asl_m',
  'quality_index',
  'quality_level',
  'peak_quality_index',
  'peak_quality_level',
  'dominant_burnable_pressure_family',
  'confidence_score',
  'best_time_local',
  'burnable_cloud_coverage_effective',
  'forward_view_cloud_field_amount_peak',
  'forward_burnable_cloud_field_amount_peak',
  'forward_burnable_cloud_ratio_peak',
  'burnable_duration_minutes',
  'local_aod550',
  'quality_aod550',
  'incoming_aod_illumination_factor',
  'observer_aod_contrast_factor',
  'observer_return_optical_depth_mean',
  'observer_return_transmission_mean',
  'incoming_aod_adjustment',
  'observer_aod_adjustment',
  'high_cloud',
  'mid_cloud',
  'low_cloud',
] as const;

const REGIONAL_SUMMARY_FIELDS = [
  'version',
  'scope',
  'point_mode',
  'regions',
  'coverage',
  'target_date',
  'phenomenon',
  'model_source',
  'algorithm',
  'cycle_time_utc',
  'forecast_mode',
  'compute_backend_requested',
  'compute_backend',
  'precision',
  'timing',
  'point_count',
  'success_count',
  'error_count',
  'point_format',
  'point_fields',
  'points',
  'errors',
  'cache_recommendation',
  'cache',
  'data_provenance',
] as const;

const REGIONAL_COVERAGE_FIELDS = [
  'code', 'label', 'bounds', 'timezone_name', 'point_prefix',
] as const;

const REGIONAL_PRECISION_FIELDS = [
  'mode',
  'point_spacing_deg',
  'model_grid_spacing_deg',
  'point_count',
  'processed_sample_count',
  'candidate_limit_per_sample',
  'vertical_profiles',
  'full_point_coverage',
  'aerosol_profile_mode',
] as const;

const REGIONAL_TIMING_FIELDS = [
  'prefetch_seconds',
  'tensor_seconds',
  'compute_seconds',
  'frames_seconds',
  'frame_instant_scoring_seconds',
  'frame_assembly_seconds',
  'frame_points_per_second',
  'frame_samples_per_second',
  'total_seconds',
] as const;

const REGIONAL_CACHE_FIELDS = ['status', 'key', 'generated_at_utc', 'version'] as const;
const REGIONAL_PROVENANCE_FIELDS = [
  'cache_status',
  'cams_source',
  'generated_at_utc',
  'stale',
  'inputs_complete',
  'requested_gfs_cycle_utc',
  'served_gfs_cycle_utc',
  'gfs_forecast_hours',
  'gfs_valid_times_utc',
  'cams_run_times_utc',
  'cams_valid_times_utc',
  'algorithm',
] as const;
const REGIONAL_ERROR_FIELDS = ['name', 'lat', 'lon', 'error'] as const;
const EXTERNAL_REGIONAL_SUMMARY_FIELDS = [
  'version', 'algorithm', 'scope', 'point_mode', 'regions', 'coverage',
  'target_date', 'phenomenon', 'model_source', 'model_resolution',
  'cycle_time_utc', 'forecast_hours', 'points', 'point_fields',
  'point_format', 'point_count', 'success_count', 'error_count', 'errors',
  'precision', 'timing', 'cache', 'data_provenance',
] as const;
const EXTERNAL_REGIONAL_PROVENANCE_FIELDS = [
  'model_source', 'cycle_utc', 'forecast_hours', 'model_files', 'cams_files',
] as const;
const REGIONAL_AREA_CODES = new Set([
  'china',
  'east-asia',
  'southeast-asia',
  'south-asia',
  'europe',
  'north-america',
  'south-america',
  'oceania',
  'africa-middle-east',
  'north-asia',
  'custom',
]);
const SHARED_TIMEZONE_AREA_CODES = new Set(['china', 'custom']);
const NATIONAL_SUMMARY_VERSION = 55;
const NATIONAL_CACHE_VERSION = 125;
const NATIONAL_BATCH_ALGORITHM = 'national-batch-2026.11.04.87-raytraced-sky-v61-isolated-column-recovery-window-supported-regional-coherent-sparse-upper-high-aod-separated-deep-target-self-transmission-opacity-only-bilinear-point-aod-service-unified-quality-isolated-aggregate-high-support-surface-gated-continuous-column-recovery-per-cloud-material-opacity-horizontal-lightpath-bilinear-v1-cross-section-aggregate-boundary-v1-terrain-profile-v1-adaptive-upper-middle-400-continuous-v2-observer-return-terrain-v1-spherical-observer-horizon-v1-cumulus-low-support-v1-isolated-aggregate-representative-300-v1-low-unsampled-layer-recovery-v1-grib-bitmap-v1-cams-aerext532-11level-aod550-normalized-segment-v1-near-low-return-diagnostic-v1';
const NOWCAST_ALGORITHM = 'satellite-nowcast-2026.11.04.87-hsd-only-multi-observation-aerosol-ground-touchdown-coverage-gate-v4';
const NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION = 4;
const SATELLITE_CLOUD_COLUMN_ALGORITHM = 'ahi-calibrated-cloud-column-v4';

interface RegionalForecastContract {
  scope: 'regional';
  algorithm: string;
  summary_version: number;
  cache_version: number;
  point_format: string;
  contract?: string;
  runtime_python_used?: boolean;
}

function validateRegionalForecastContract(payload: unknown): void {
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('区域预测合同响应无效，任务未启动。');
  }
  const contract = payload as Partial<RegionalForecastContract>;
  if (contract.contract === 'rust-desktop-regional-forecast-v1') {
    if (
      contract.scope !== 'regional'
      || contract.point_format !== 'native-object-v1'
      || contract.runtime_python_used !== false
    ) {
      throw new Error('Native regional forecast contract is invalid');
    }
    return;
  }
  if (
    contract.scope !== 'regional'
    || typeof contract.algorithm !== 'string'
    || typeof contract.summary_version !== 'number'
    || typeof contract.cache_version !== 'number'
    || typeof contract.point_format !== 'string'
  ) {
    throw new Error('区域预测合同响应无效，任务未启动。');
  }
  if (contract.algorithm !== NATIONAL_BATCH_ALGORITHM) {
    throw new Error(
      `区域预测算法版本不一致：前端 ${NATIONAL_BATCH_ALGORITHM}，后端 ${contract.algorithm}。请刷新前端后重试。`,
    );
  }
  if (
    contract.summary_version !== NATIONAL_SUMMARY_VERSION
    || contract.cache_version !== NATIONAL_CACHE_VERSION
    || contract.point_format !== 'array-v6'
  ) {
    throw new Error('区域预测数据合同版本不一致，任务未启动，请刷新前端后重试。');
  }
}

export function ensureRegionalForecastContract(signal?: AbortSignal): Promise<void> {
  return get<RegionalForecastContract>(
    '/api/v1/firesky/forecasts/national/contract',
    undefined,
    signal,
  ).then((payload) => {
    validateRegionalForecastContract(payload);
  });
}

const NOWCAST_CLOUD_COLUMN_FIELDS = [
  'schema_version', 'algorithm', 'valid_time_utc', 'lat', 'lon', 'distance_km',
  'bearing_deg', 'base_p10_asl_m', 'base_p50_asl_m', 'base_p90_asl_m',
  'base_p10_agl_m', 'base_p50_agl_m', 'base_p90_agl_m', 'top_asl_m', 'top_agl_m',
  'terrain_asl_m', 'phase', 'cloud_type', 'optical_depth',
  'cloud_probability_by_height', 'horizontal_cloud_probability',
  'low_cloud_state', 'retrieval_quality', 'source',
  'vertical_prior_identity', 'vertical_prior_source', 'base_source',
  'motion_dx_px_per_hour', 'motion_dy_px_per_hour',
] as const;
const NOWCAST_CLOUD_COLUMN_SET_FIELDS = [
  'schema_version', 'valid_time_utc', 'source', 'vertical_prior_identity',
  'retrieval_quality', 'low_cloud_state', 'motion_dx_px_per_hour',
  'motion_dy_px_per_hour', 'columns',
] as const;
const NOWCAST_REQUIRED_FIELDS = [
  'nowcast_algorithm', 'cloud_column_schema_version', 'cloud_column_sets',
  'vertical_prior_identity', 'vertical_prior_source', 'satellite_retrieval_quality',
  'satellite_low_cloud_state', 'satellite_range_bytes', 'satellite_range_blocks',
  'nowcast_point_lat', 'nowcast_point_lon', 'nowcast_cache_identity',
  'satellite_source_times_utc', 'satellite_motion_quality', 'satellite_advection_horizon_hours',
] as const;

function requireExactObject(
  value: unknown,
  fields: readonly string[],
  label: string,
): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  const object = value as Record<string, unknown>;
  const keys = Object.keys(object);
  if (
    keys.length !== fields.length
    || fields.some((field) => !Object.prototype.hasOwnProperty.call(object, field))
  ) {
    throw new Error(`${label} fields do not match the current contract`);
  }
  return object;
}

function requireFiniteNumber(value: unknown, label: string, minimum?: number): number {
  if (
    typeof value !== 'number'
    || !Number.isFinite(value)
    || (minimum !== undefined && value < minimum)
  ) {
    throw new Error(`${label} must be a finite number`);
  }
  return value;
}

function requireNonNegativeInteger(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new Error(`${label} must be a non-negative integer`);
  }
  return value;
}

function requireNonEmptyString(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

export function validateNowcastCloudContract(value: unknown): asserts value is NowcastInfo {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Nowcast payload must be an object');
  }
  const info = value as Record<string, unknown>;
  if (Object.prototype.hasOwnProperty.call(info, 'satellite_range_requests')) {
    throw new Error('Nowcast range_requests field is obsolete');
  }
  for (const field of NOWCAST_REQUIRED_FIELDS) {
    if (!Object.prototype.hasOwnProperty.call(info, field)) {
      throw new Error(`Nowcast payload is missing ${field}`);
    }
  }
  if (info.cloud_column_schema_version !== NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION) {
    throw new Error('Nowcast cloud-column schema is obsolete');
  }
  if (!['detected', 'clear', 'unknown'].includes(String(info.satellite_low_cloud_state))) {
    throw new Error('Nowcast low-cloud state is invalid');
  }
  if (!['high', 'medium', 'low', 'unknown'].includes(String(info.satellite_retrieval_quality))) {
    throw new Error('Nowcast retrieval quality is invalid');
  }
  if (info.nowcast_algorithm !== NOWCAST_ALGORITHM) {
    throw new Error('Nowcast algorithm is obsolete');
  }
  requireNonEmptyString(info.vertical_prior_identity, 'Nowcast vertical prior identity');
  requireNonEmptyString(info.vertical_prior_source, 'Nowcast vertical prior source');
  requireNonEmptyString(info.nowcast_cache_identity, 'Nowcast cache identity');
  requireFiniteNumber(info.nowcast_point_lat, 'Nowcast point latitude');
  requireFiniteNumber(info.nowcast_point_lon, 'Nowcast point longitude');
  requireNonNegativeInteger(info.satellite_range_bytes, 'Nowcast range bytes');
  requireNonNegativeInteger(info.satellite_range_blocks, 'Nowcast range blocks');
  requireStrictUtcIsoTime(info.image_valid_time_utc, 'Nowcast image_valid_time_utc');
  if (!Array.isArray(info.satellite_source_times_utc)) {
    throw new Error('Nowcast satellite source times are invalid');
  }
  const sourceTimes = info.satellite_source_times_utc.map((value, sourceIndex) => {
    const sourceTime = requireStrictUtcIsoTime(
      value,
      `Nowcast satellite source time ${sourceIndex}`,
    );
    return Date.parse(sourceTime);
  });
  if (new Set(sourceTimes).size !== sourceTimes.length) {
    throw new Error('Nowcast satellite source times must be unique');
  }
  if (!['unknown', 'observation', 'strong', 'decayed', 'diagnostic-only'].includes(String(info.satellite_motion_quality))) {
    throw new Error('Nowcast satellite motion quality is invalid');
  }
  requireFiniteNumber(info.satellite_advection_horizon_hours, 'Nowcast satellite advection horizon', 0);
  if (!Array.isArray(info.cloud_column_sets)) {
    throw new Error('Nowcast cloud-column sets are invalid');
  }
  info.cloud_column_sets.forEach((rawSet, setIndex) => {
    const set = requireExactObject(
      rawSet,
      NOWCAST_CLOUD_COLUMN_SET_FIELDS,
      `Nowcast cloud-column set ${setIndex}`,
    );
    if (set.schema_version !== NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION || !Array.isArray(set.columns)) {
      throw new Error(`Nowcast cloud-column set ${setIndex} schema is invalid`);
    }
    const setTime = requireStrictUtcIsoTime(
      set.valid_time_utc,
      `Nowcast cloud-column set ${setIndex}.valid_time_utc`,
    );
    requireNonEmptyString(set.source, `Nowcast cloud-column set ${setIndex}.source`);
    requireNonEmptyString(set.vertical_prior_identity, `Nowcast cloud-column set ${setIndex}.vertical_prior_identity`);
    if (!['detected', 'clear', 'unknown'].includes(String(set.low_cloud_state))) {
      throw new Error(`Nowcast cloud-column set ${setIndex}.low_cloud_state is invalid`);
    }
    if (!['high', 'medium', 'low', 'unknown'].includes(String(set.retrieval_quality))) {
      throw new Error(`Nowcast cloud-column set ${setIndex}.retrieval_quality is invalid`);
    }
    set.columns.forEach((rawColumn, columnIndex) => {
      const column = requireExactObject(
        rawColumn,
        NOWCAST_CLOUD_COLUMN_FIELDS,
        `Nowcast cloud column ${setIndex}.${columnIndex}`,
      );
      if (column.schema_version !== NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION || column.phase === undefined) {
        throw new Error(`Nowcast cloud column ${setIndex}.${columnIndex} schema is invalid`);
      }
      validateNowcastCloudColumn(column, set, setIndex, columnIndex);
      const columnTime = requireStrictUtcIsoTime(
        column.valid_time_utc,
        `Nowcast cloud column ${setIndex}.${columnIndex}.valid_time_utc`,
      );
      if (!['water', 'ice', 'mixed', 'unknown'].includes(String(column.phase))) {
        throw new Error(`Nowcast cloud column ${setIndex}.${columnIndex}.phase is invalid`);
      }
      if (!['detected', 'clear', 'unknown'].includes(String(column.low_cloud_state))) {
        throw new Error(`Nowcast cloud column ${setIndex}.${columnIndex}.low_cloud_state is invalid`);
      }
      if (!['high', 'medium', 'low', 'unknown'].includes(String(column.retrieval_quality))) {
        throw new Error(`Nowcast cloud column ${setIndex}.${columnIndex}.retrieval_quality is invalid`);
      }
      requireNonEmptyString(column.vertical_prior_identity, `Nowcast cloud column ${setIndex}.${columnIndex}.vertical_prior_identity`);
      requireNonEmptyString(column.vertical_prior_source, `Nowcast cloud column ${setIndex}.${columnIndex}.vertical_prior_source`);
      if (column.valid_time_utc !== set.valid_time_utc) {
        throw new Error(`Nowcast cloud column ${setIndex}.${columnIndex} time does not match set`);
      }
      if (column.source !== set.source) {
        throw new Error(`Nowcast cloud column ${setIndex}.${columnIndex} source does not match set`);
      }
      if (Date.parse(columnTime) !== Date.parse(setTime)) {
        throw new Error(`Nowcast cloud column ${setIndex}.${columnIndex} time does not match set`);
      }
    });
  });
  const allowedSetTimes = new Set([
    ...sourceTimes,
    Date.parse(String(info.image_valid_time_utc)),
  ]);
  for (const [setIndex, rawSet] of info.cloud_column_sets.entries()) {
    const set = rawSet as Record<string, unknown>;
    const setTime = Date.parse(String(set.valid_time_utc));
    if (!allowedSetTimes.has(setTime)) {
      throw new Error(`Nowcast cloud-column set ${setIndex} has no source scan provenance`);
    }
  }
  if (info.satellite_vertical_profile !== undefined && info.satellite_vertical_profile !== null) {
    validateSatelliteVerticalProfile(info.satellite_vertical_profile);
  }
}

function validateSatelliteVerticalProfile(value: unknown): void {
  const profile = requireExactObject(
    value,
    [
      'schema_version', 'status', 'source', 'valid_time_utc', 'bands', 'prior_source',
      'sample_count', 'usable_sample_count', 'unknown_sample_count', 'coverage',
      'numeric_clear_sky_allowed', 'pressure_levels_hpa', 'samples',
    ],
    'Satellite vertical profile',
  );
  if (profile.schema_version !== 1 || !['available', 'partial_unknown', 'unavailable'].includes(String(profile.status))) {
    throw new Error('Satellite vertical profile identity is invalid');
  }
  requireNonEmptyString(profile.source, 'Satellite vertical profile source');
  requireStrictUtcIsoTime(profile.valid_time_utc, 'Satellite vertical profile valid time');
  const bands = Array.isArray(profile.bands) ? profile.bands.map(String) : [];
  const isHsd = JSON.stringify(bands) === JSON.stringify(['B07', 'B13', 'B15']);
  const isHcai = JSON.stringify(bands) === JSON.stringify([
    'CLOUD_MASK', 'CLOUD_PROBABILITY', 'CLOUD_MASK_QF', 'CLOUD_PHASE',
    'CLOUD_TYPE', 'CLOUD_TOP_HEIGHT', 'CLOUD_OPTICAL_DEPTH', 'CLOUD_HEIGHT_QF',
  ]);
  if ((!isHsd && !isHcai) || profile.prior_source !== 'gfs') {
    throw new Error('Satellite vertical profile provenance is invalid');
  }
  for (const field of ['sample_count', 'usable_sample_count', 'unknown_sample_count']) {
    requireNonNegativeInteger(profile[field], `Satellite vertical profile ${field}`);
  }
  const coverage = requireFiniteNumber(profile.coverage, 'Satellite vertical profile coverage');
  if (coverage < 0 || coverage > 1 || profile.numeric_clear_sky_allowed !== false) {
    throw new Error('Satellite vertical profile coverage contract is invalid');
  }
  if (
    !Array.isArray(profile.pressure_levels_hpa)
    || profile.pressure_levels_hpa.some((pressure) => !Number.isInteger(pressure) || pressure <= 0)
    || !Array.isArray(profile.samples)
    || profile.samples.length !== profile.sample_count
  ) {
    throw new Error('Satellite vertical profile levels are invalid');
  }
  const pressureLevels = profile.pressure_levels_hpa as unknown[];
  profile.samples.forEach((rawSample, sampleIndex) => {
    const sample = requireExactObject(
      rawSample,
      [
        'distance_km', 'bearing_deg', 'status', 'retrieval_quality',
        'vertical_prior_identity', 'vertical_prior_source', 'levels',
      ],
      `Satellite vertical profile sample ${sampleIndex}`,
    );
    requireFiniteNumber(sample.distance_km, `Satellite vertical profile sample ${sampleIndex}.distance_km`, 0);
    const bearing = requireFiniteNumber(sample.bearing_deg, `Satellite vertical profile sample ${sampleIndex}.bearing_deg`);
    if (bearing < 0 || bearing >= 360 || !['retrieved', 'unknown'].includes(String(sample.status))) {
      throw new Error(`Satellite vertical profile sample ${sampleIndex} coordinates/status are invalid`);
    }
    requireNonEmptyString(sample.vertical_prior_identity, `Satellite vertical profile sample ${sampleIndex}.vertical_prior_identity`);
    requireNonEmptyString(sample.vertical_prior_source, `Satellite vertical profile sample ${sampleIndex}.vertical_prior_source`);
    if (
      !['high', 'medium', 'low', 'unknown'].includes(String(sample.retrieval_quality))
      || !Array.isArray(sample.levels)
      || sample.levels.length !== pressureLevels.length
    ) {
      throw new Error(`Satellite vertical profile sample ${sampleIndex} levels are invalid`);
    }
    (sample.levels as unknown[]).forEach((rawLevel, levelIndex) => {
      const level = rawLevel as Record<string, unknown>;
      if (level === null || typeof level !== 'object' || Array.isArray(level)) {
        throw new Error(`Satellite vertical profile level ${sampleIndex}.${levelIndex} is invalid`);
      }
      if (
        level.pressure_hpa !== pressureLevels[levelIndex]
        || !['retrieved', 'unknown'].includes(String(level.status))
      ) {
        throw new Error(`Satellite vertical profile level ${sampleIndex}.${levelIndex} identity is invalid`);
      }
      for (const field of [
        'height_m', 'rh_percent', 'temperature_c', 'model_cloud_fraction',
        'model_cloud_fraction_percent', 'hsd_posterior', 'hsd_posterior_percent',
        'cloud_probability_percent', 'horizontal_cloud_probability',
      ]) {
        const fieldValue = level[field];
        if (fieldValue !== null && fieldValue !== undefined) {
          requireFiniteNumber(fieldValue, `Satellite vertical profile level ${sampleIndex}.${levelIndex}.${field}`);
        }
      }
      for (const [field, minimum, maximum] of [
        ['rh_percent', 0, 100],
        ['model_cloud_fraction_percent', 0, 100],
        ['model_cloud_fraction', 0, 1],
        ['hsd_posterior_percent', 0, 100],
        ['hsd_posterior', 0, 1],
        ['cloud_probability_percent', 0, 100],
        ['horizontal_cloud_probability', 0, 1],
      ] as const) {
        const fieldValue = level[field];
        const numericValue = fieldValue === null || fieldValue === undefined
          ? null
          : requireFiniteNumber(fieldValue, `Satellite vertical profile level ${sampleIndex}.${levelIndex}.${field}`);
        if (numericValue !== null && (numericValue < minimum || numericValue > maximum)) {
          throw new Error(`Satellite vertical profile level ${sampleIndex}.${levelIndex}.${field} is out of range`);
        }
      }
    });
  });
}

function validateNowcastCloudColumn(
  column: Record<string, unknown>,
  set: Record<string, unknown>,
  setIndex: number,
  columnIndex: number,
): void {
  const label = 'Nowcast cloud column ' + setIndex + '.' + columnIndex;
  if (column.algorithm !== SATELLITE_CLOUD_COLUMN_ALGORITHM) {
    throw new Error(label + ' algorithm is obsolete');
  }
  requireStrictUtcIsoTime(column.valid_time_utc, label + '.valid_time_utc');
  const lat = requireFiniteNumber(column.lat, label + '.lat');
  const lon = requireFiniteNumber(column.lon, label + '.lon');
  requireFiniteNumber(column.distance_km, label + '.distance_km', 0);
  const bearing = requireFiniteNumber(column.bearing_deg, label + '.bearing_deg');
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180 || bearing < 0 || bearing >= 360) {
    throw new Error(label + ' coordinates are invalid');
  }
  const terrain = column.terrain_asl_m === null
    ? null
    : requireFiniteNumber(column.terrain_asl_m, label + '.terrain_asl_m');
  const p10 = column.base_p10_asl_m === null
    ? null
    : requireFiniteNumber(column.base_p10_asl_m, label + '.base_p10_asl_m');
  const p50 = column.base_p50_asl_m === null
    ? null
    : requireFiniteNumber(column.base_p50_asl_m, label + '.base_p50_asl_m');
  const p90 = column.base_p90_asl_m === null
    ? null
    : requireFiniteNumber(column.base_p90_asl_m, label + '.base_p90_asl_m');
  const top = column.top_asl_m === null
    ? null
    : requireFiniteNumber(column.top_asl_m, label + '.top_asl_m');
  if (
    (p10 !== null && p50 !== null && p10 > p50)
    || (p50 !== null && p90 !== null && p50 > p90)
    || (p90 !== null && top !== null && p90 > top)
    || (terrain !== null && p10 !== null && p10 < terrain)
    || (terrain !== null && top !== null && top < terrain)
  ) {
    throw new Error(label + ' height order is invalid');
  }
  const horizontal = column.horizontal_cloud_probability === null
    ? null
    : requireFiniteNumber(column.horizontal_cloud_probability, label + '.horizontal_cloud_probability');
  if (horizontal !== null && (horizontal < 0 || horizontal > 1)) {
    throw new Error(label + ' horizontal probability is invalid');
  }
  if (column.optical_depth !== null) {
    const opticalDepth = requireFiniteNumber(column.optical_depth, label + '.optical_depth');
    if (opticalDepth < 0 || opticalDepth > 20) {
      throw new Error(label + ' optical depth is invalid');
    }
  }
  if (
    typeof column.cloud_probability_by_height !== 'object'
    || column.cloud_probability_by_height === null
    || Array.isArray(column.cloud_probability_by_height)
  ) {
    throw new Error(label + ' probability profile is invalid');
  }
  for (const [heightKey, rawProbability] of Object.entries(
    column.cloud_probability_by_height as Record<string, unknown>,
  )) {
    const height = Number(heightKey);
    const probability = requireFiniteNumber(
      rawProbability,
      label + '.cloud_probability_by_height.' + heightKey,
    );
    if (
      !Number.isFinite(height)
      || probability < 0
      || probability > 1
      || (terrain !== null && height < terrain)
      || (top !== null && height > top + 500)
    ) {
      throw new Error(label + ' probability profile is invalid');
    }
  }
  requireFiniteNumber(column.motion_dx_px_per_hour, label + '.motion_dx_px_per_hour');
  requireFiniteNumber(column.motion_dy_px_per_hour, label + '.motion_dy_px_per_hour');
  if (column.vertical_prior_identity !== set.vertical_prior_identity) {
    throw new Error(label + ' vertical prior identity does not match set');
  }
}

function isValidIanaTimezone(value: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: value }).format();
    return true;
  } catch {
    return false;
  }
}

function validateRegionalSummary(payload: unknown): Record<string, unknown> {
  const summary = requireExactObject(payload, REGIONAL_SUMMARY_FIELDS, 'Regional summary53');
  if (summary.version !== NATIONAL_SUMMARY_VERSION || summary.scope !== 'regional') {
    throw new Error('Regional summary version/scope does not match summary53');
  }
  if (
    summary.point_mode !== 'explicit-single-points'
    && summary.point_mode !== 'generated-regional-grid'
  ) {
    throw new Error('Regional summary point_mode is invalid');
  }
  if (summary.phenomenon !== 'dawn' && summary.phenomenon !== 'dusk') {
    throw new Error('Regional summary phenomenon is invalid');
  }
  if (summary.model_source !== 'gfs') {
    throw new Error('Regional summary model_source is invalid');
  }
  if (summary.forecast_mode !== 'model' && summary.forecast_mode !== 'nowcast') {
    throw new Error('Regional summary forecast_mode is invalid');
  }
  if (!['auto', 'cpu', 'gpu'].includes(String(summary.compute_backend_requested))) {
    throw new Error('Regional summary compute_backend_requested is invalid');
  }
  if (!['cpu', 'gpu', 'hybrid'].includes(String(summary.compute_backend))) {
    throw new Error('Regional summary compute_backend is invalid');
  }
  if (summary.algorithm !== NATIONAL_BATCH_ALGORITHM) {
    throw new Error('Regional summary algorithm is not current');
  }
  requireStrictUtcIsoTime(summary.cycle_time_utc, 'Regional summary cycle_time_utc');
  requireNonEmptyString(summary.cache_recommendation, 'Regional summary cache_recommendation');
  requireStrictIsoDate(summary.target_date, 'Regional summary target_date');

  const provenance = requireExactObject(
    summary.data_provenance,
    REGIONAL_PROVENANCE_FIELDS,
    'Regional summary data_provenance',
  );
  if (typeof provenance.stale !== 'boolean' || typeof provenance.inputs_complete !== 'boolean') {
    throw new Error('Regional summary data_provenance flags are invalid');
  }
  if (!['refresh', 'hit', 'stale'].includes(String(provenance.cache_status))) {
    throw new Error('Regional summary data_provenance.cache_status is invalid');
  }
  if (provenance.cams_source !== null && provenance.cams_source !== 'cams') {
    throw new Error('Regional summary data_provenance.cams_source is invalid');
  }
  requireStrictUtcIsoTime(
    provenance.generated_at_utc,
    'Regional summary data_provenance.generated_at_utc',
  );
  requireStrictUtcIsoTime(
    provenance.requested_gfs_cycle_utc,
    'Regional summary requested GFS cycle',
  );
  requireStrictUtcIsoTime(
    provenance.served_gfs_cycle_utc,
    'Regional summary served GFS cycle',
  );
  if (provenance.served_gfs_cycle_utc !== summary.cycle_time_utc) {
    throw new Error('Regional summary served GFS cycle does not match cycle_time_utc');
  }
  if (provenance.algorithm !== NATIONAL_BATCH_ALGORITHM) {
    throw new Error('Regional summary data_provenance algorithm is not current');
  }
  if (
    !Array.isArray(provenance.gfs_forecast_hours)
    || provenance.gfs_forecast_hours.some((value) => !Number.isInteger(value) || Number(value) < 0)
  ) {
    throw new Error('Regional summary GFS forecast hours are invalid');
  }
  for (const field of ['gfs_valid_times_utc', 'cams_run_times_utc', 'cams_valid_times_utc'] as const) {
    const values = provenance[field];
    if (!Array.isArray(values) || values.some((value) => typeof value !== 'string')) {
      throw new Error(`Regional summary ${field} is invalid`);
    }
    values.forEach((value) => requireStrictUtcIsoTime(value, `Regional summary ${field}`));
  }
  const gfsForecastHours = provenance.gfs_forecast_hours as number[];
  const gfsValidTimes = provenance.gfs_valid_times_utc as string[];
  const camsRunTimes = provenance.cams_run_times_utc as string[];
  const camsValidTimes = provenance.cams_valid_times_utc as string[];
  if (
    provenance.inputs_complete
    && (
      gfsForecastHours.length === 0
      || gfsValidTimes.length === 0
      || camsRunTimes.length === 0
      || camsValidTimes.length === 0
    )
  ) {
    throw new Error('Complete regional provenance requires GFS and CAMS times');
  }
  if (!provenance.inputs_complete && provenance.cams_source !== null) {
    throw new Error('Incomplete regional provenance cannot claim a CAMS source');
  }

  const regions = summary.regions;
  if (
    !Array.isArray(regions)
    || regions.length === 0
    || regions.some((code) => typeof code !== 'string' || !REGIONAL_AREA_CODES.has(code))
    || new Set(regions).size !== regions.length
  ) {
    throw new Error('Regional summary regions are invalid');
  }
  const coverage = summary.coverage;
  if (!Array.isArray(coverage) || coverage.length !== regions.length) {
    throw new Error('Regional summary coverage does not match regions');
  }
  coverage.forEach((rawArea, index) => {
    const area = requireExactObject(
      rawArea,
      REGIONAL_COVERAGE_FIELDS,
      `Regional summary coverage ${index}`,
    );
    const code = area.code;
    if (typeof code !== 'string' || code !== regions[index] || !REGIONAL_AREA_CODES.has(code)) {
      throw new Error(`Regional summary coverage ${index} has invalid code`);
    }
    requireNonEmptyString(area.label, `Regional summary coverage ${index}.label`);
    requireNonEmptyString(area.point_prefix, `Regional summary coverage ${index}.point_prefix`);
    if (!Array.isArray(area.bounds) || area.bounds.length !== 4) {
      throw new Error(`Regional summary coverage ${index}.bounds is invalid`);
    }
    const [west, south, east, north] = area.bounds.map((value, boundIndex) =>
      requireFiniteNumber(value, `Regional summary coverage ${index}.bounds[${boundIndex}]`));
    if (!(west >= -180 && west < east && east <= 180 && south >= -90 && south < north && north <= 90)) {
      throw new Error(`Regional summary coverage ${index}.bounds is invalid`);
    }
    if (SHARED_TIMEZONE_AREA_CODES.has(code)) {
      if (typeof area.timezone_name !== 'string' || !isValidIanaTimezone(area.timezone_name)) {
        throw new Error(`Regional summary coverage ${index}.timezone_name is invalid`);
      }
    } else if (area.timezone_name !== null) {
      throw new Error(`Regional summary coverage ${index} must use per-point timezones`);
    }
  });

  const precision = requireExactObject(
    summary.precision,
    REGIONAL_PRECISION_FIELDS,
    'Regional summary precision',
  );
  requireNonEmptyString(precision.mode, 'Regional summary precision.mode');
  if (precision.point_spacing_deg !== null) {
    const pointSpacing = requireFiniteNumber(
      precision.point_spacing_deg,
      'Regional summary precision.point_spacing_deg',
      0,
    );
    if (pointSpacing <= 0) {
      throw new Error('Regional summary precision.point_spacing_deg must be positive');
    }
  }
  const modelGridSpacing = requireFiniteNumber(
    precision.model_grid_spacing_deg,
    'Regional summary precision.model_grid_spacing_deg',
    0,
  );
  if (modelGridSpacing <= 0) {
    throw new Error('Regional summary precision.model_grid_spacing_deg must be positive');
  }
  for (const field of ['point_count', 'processed_sample_count', 'candidate_limit_per_sample']) {
    requireNonNegativeInteger(precision[field], `Regional summary precision.${field}`);
  }
  if (typeof precision.vertical_profiles !== 'boolean' || typeof precision.full_point_coverage !== 'boolean') {
    throw new Error('Regional summary precision flags are invalid');
  }
  requireNonEmptyString(
    precision.aerosol_profile_mode,
    'Regional summary precision.aerosol_profile_mode',
  );

  const timing = requireExactObject(summary.timing, REGIONAL_TIMING_FIELDS, 'Regional summary timing');
  for (const field of REGIONAL_TIMING_FIELDS) {
    requireFiniteNumber(timing[field], `Regional summary timing.${field}`, 0);
  }
  const cache = requireExactObject(summary.cache, REGIONAL_CACHE_FIELDS, 'Regional summary cache');
  if (cache.status !== 'refresh' && cache.status !== 'hit' && cache.status !== 'stale') {
    throw new Error('Regional summary cache.status is invalid');
  }
  if ((cache.status === 'stale') !== provenance.stale) {
    throw new Error('Regional summary stale status does not match data_provenance');
  }
  if (cache.status !== provenance.cache_status) {
    throw new Error('Regional summary cache status does not match data_provenance');
  }
  requireNonEmptyString(cache.key, 'Regional summary cache.key');
  requireStrictUtcIsoTime(
    cache.generated_at_utc,
    'Regional summary cache.generated_at_utc',
  );
  if (cache.version !== NATIONAL_CACHE_VERSION) {
    throw new Error('Regional summary cache.version is invalid');
  }

  const pointCount = requireNonNegativeInteger(summary.point_count, 'Regional summary point_count');
  const successCount = requireNonNegativeInteger(summary.success_count, 'Regional summary success_count');
  const errorCount = requireNonNegativeInteger(summary.error_count, 'Regional summary error_count');
  if (!Array.isArray(summary.points) || summary.points.length !== successCount) {
    throw new Error('Regional summary points do not match success_count');
  }
  if (pointCount !== successCount + errorCount || precision.point_count !== pointCount) {
    throw new Error('Regional summary outcome counts are inconsistent');
  }
  if (!Array.isArray(summary.errors) || summary.errors.length !== errorCount) {
    throw new Error('Regional summary errors do not match error_count');
  }
  requireProductionChinaSummary(summary, precision);
  summary.errors.forEach((rawError, index) => {
    const error = requireExactObject(
      rawError,
      REGIONAL_ERROR_FIELDS,
      `Regional summary error ${index}`,
    );
    requireNonEmptyString(error.name, `Regional summary error ${index}.name`);
    requireNonEmptyString(error.error, `Regional summary error ${index}.error`);
    requireFiniteNumber(error.lat, `Regional summary error ${index}.lat`);
    requireFiniteNumber(error.lon, `Regional summary error ${index}.lon`);
    if (Number(error.lat) < -90 || Number(error.lat) > 90) {
      throw new Error(`Regional summary error ${index}.lat is invalid`);
    }
    if (Number(error.lon) < -180 || Number(error.lon) > 180) {
      throw new Error(`Regional summary error ${index}.lon is invalid`);
    }
  });
  return summary;
}

function validateExternalRegionalSummary(payload: unknown): Record<string, unknown> {
  const summary = requireExactObject(
    payload,
    EXTERNAL_REGIONAL_SUMMARY_FIELDS,
    'External regional summary v1',
  );
  if (
    summary.version !== 1
    || summary.scope !== 'regional'
    || summary.model_source !== 'ec'
    || summary.model_resolution !== '0p25'
    || summary.point_format !== 'array-v6'
  ) {
    throw new Error('External regional summary identity does not match v1');
  }
  if (summary.phenomenon !== 'dawn' && summary.phenomenon !== 'dusk') {
    throw new Error('External regional summary phenomenon is invalid');
  }
  requireStrictIsoDate(summary.target_date, 'External regional summary target_date');
  requireStrictUtcIsoTime(summary.cycle_time_utc, 'External regional summary cycle_time_utc');
  if (
    !Array.isArray(summary.forecast_hours)
    || summary.forecast_hours.length === 0
    || summary.forecast_hours.some((value) => !Number.isInteger(value) || value < 0)
    || summary.forecast_hours.some((value, index, values) => index > 0 && value <= values[index - 1])
  ) {
    throw new Error('External regional summary forecast_hours are invalid');
  }
  const provenance = requireExactObject(
    summary.data_provenance,
    EXTERNAL_REGIONAL_PROVENANCE_FIELDS,
    'External regional summary data_provenance',
  );
  if (
    provenance.model_source !== summary.model_source
    || provenance.cycle_utc !== summary.cycle_time_utc
    || JSON.stringify(provenance.forecast_hours) !== JSON.stringify(summary.forecast_hours)
    || !Array.isArray(provenance.model_files)
    || provenance.model_files.length === 0
    || provenance.model_files.some((value) => typeof value !== 'string' || value.length === 0)
    || !Array.isArray(provenance.cams_files)
    || provenance.cams_files.length === 0
    || provenance.cams_files.some((value) => typeof value !== 'string' || value.length === 0)
  ) {
    throw new Error('External regional summary provenance is incomplete');
  }
  requireStrictUtcIsoTime(provenance.cycle_utc, 'External regional summary provenance cycle_utc');

  if (
    !Array.isArray(summary.regions)
    || summary.regions.length === 0
    || !Array.isArray(summary.coverage)
    || summary.coverage.length !== summary.regions.length
  ) {
    throw new Error('External regional summary coverage is invalid');
  }
  const pointCount = requireNonNegativeInteger(summary.point_count, 'External regional summary point_count');
  const successCount = requireNonNegativeInteger(summary.success_count, 'External regional summary success_count');
  const errorCount = requireNonNegativeInteger(summary.error_count, 'External regional summary error_count');
  if (
    !Array.isArray(summary.points)
    || summary.points.length !== successCount
    || pointCount !== successCount + errorCount
    || !Array.isArray(summary.errors)
    || summary.errors.length !== errorCount
  ) {
    throw new Error('External regional summary outcome counts are inconsistent');
  }
  requireExactObject(summary.precision, REGIONAL_PRECISION_FIELDS, 'External regional summary precision');
  requireExactObject(summary.timing, REGIONAL_TIMING_FIELDS, 'External regional summary timing');
  const cache = requireExactObject(summary.cache, REGIONAL_CACHE_FIELDS, 'External regional summary cache');
  if (cache.status !== 'refresh' && cache.status !== 'hit' && cache.status !== 'stale') {
    throw new Error('External regional summary cache status is invalid');
  }
  requireNonEmptyString(cache.key, 'External regional summary cache.key');
  requireStrictUtcIsoTime(cache.generated_at_utc, 'External regional summary cache.generated_at_utc');
  if (cache.version !== NATIONAL_CACHE_VERSION) {
    throw new Error('External regional summary cache.version is invalid');
  }
  return summary;
}

function nativeRegionalPointArrays(payload: Record<string, unknown>): Record<string, unknown> {
  const native = payload.native_forecast as Record<string, unknown>;
  if (
    native.contract !== 'rust-desktop-regional-forecast-v1'
    || native.runtime_python_used !== false
    || native.point_payload_complete !== true
    || native.frame_payload_complete !== true
    || native.public_forecast_complete !== false
    || payload.scope !== 'regional'
    || (payload.model_source !== 'gfs' && payload.model_source !== 'ec')
    || payload.forecast_mode !== 'model'
  ) throw new Error('Native regional identity is invalid');
  if (
    !['dawn', 'dusk'].includes(String(payload.phenomenon))
    || !['explicit-single-points', 'generated-regional-grid'].includes(String(payload.point_mode))
    || !['auto', 'cpu', 'gpu'].includes(String(payload.compute_backend_requested))
    || payload.compute_backend !== 'cpu'
  ) throw new Error('Native regional request identity is invalid');
  requireStrictIsoDate(payload.target_date, 'Native regional target_date');
  requireStrictUtcIsoTime(payload.cycle_time_utc, 'Native regional cycle_time_utc');
  const regions = payload.regions;
  const coverage = payload.coverage;
  if (
    !Array.isArray(regions) || regions.length === 0
    || regions.some((code) => !REGIONAL_AREA_CODES.has(String(code)))
    || new Set(regions).size !== regions.length
    || !Array.isArray(coverage) || coverage.length !== regions.length
  ) throw new Error('Native regional coverage is invalid');
  coverage.forEach((raw, index) => {
    const area = requireExactObject(raw, REGIONAL_COVERAGE_FIELDS, 'Native regional coverage');
    if (area.code !== regions[index]) throw new Error('Native regional coverage identity is invalid');
    requireNonEmptyString(area.label, 'Native regional coverage label');
    requireNonEmptyString(area.point_prefix, 'Native regional coverage point_prefix');
    if (!Array.isArray(area.bounds) || area.bounds.length !== 4) {
      throw new Error('Native regional bounds are invalid');
    }
    const [west, south, east, north] = area.bounds.map((value) =>
      requireFiniteNumber(value, 'Native regional bound'));
    if (!(west >= -180 && west < east && east <= 180 && south >= -90 && south < north && north <= 90)) {
      throw new Error('Native regional bounds are invalid');
    }
    if (SHARED_TIMEZONE_AREA_CODES.has(String(area.code))) {
      if (typeof area.timezone_name !== 'string' || !isValidIanaTimezone(area.timezone_name)) {
        throw new Error('Native regional timezone is invalid');
      }
    } else if (area.timezone_name !== null) throw new Error('Native regional per-point timezone is invalid');
  });
  const points = payload.points;
  const count = requireNonNegativeInteger(payload.point_count, 'Native regional point_count');
  if (
    !Array.isArray(points) || points.length !== count
    || payload.success_count !== count || payload.error_count !== 0
    || !Array.isArray(payload.errors) || payload.errors.length !== 0
  ) throw new Error('Native regional point counts are inconsistent');
  const cache = payload.cache as Record<string, unknown>;
  if (
    cache?.contract !== 'rust-native-runtime-cache-v1'
    || !['hit', 'refresh'].includes(String(cache.status))
    || typeof cache.key !== 'string' || !/^[a-f0-9]{64}$/.test(cache.key)
    || typeof cache.source_id !== 'string' || !/^[a-f0-9]{64}$/.test(cache.source_id)
  ) throw new Error('Native regional cache identity is invalid');
  requireStrictUtcIsoTime(cache.generated_at_utc, 'Native regional cache time');
  const manifest = payload.input_manifest as Record<string, unknown>;
  if (manifest?.contract !== 'rust-runtime-input-provenance-v1' || !Array.isArray(manifest.sources)) {
    throw new Error('Native regional source manifest is invalid');
  }
  const hours: number[] = [];
  const modelFiles: string[] = [];
  const camsFiles: string[] = [];
  for (const raw of manifest.sources) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error('Native regional source identity is invalid');
    }
    const source = raw as Record<string, unknown>;
    const identity = source.identity as Record<string, unknown>;
    if (
      typeof source.path !== 'string' || !source.path
      || typeof source.role !== 'string'
      || !identity || typeof identity.sha256 !== 'string'
      || !/^[a-f0-9]{64}$/.test(identity.sha256)
      || !Number.isInteger(identity.bytes) || Number(identity.bytes) <= 0
    ) throw new Error('Native regional source identity is incomplete');
    const model = /^model:f(\d+)$/.exec(source.role);
    if (model) {
      hours.push(Number(model[1]));
      modelFiles.push(source.path);
    } else if (source.role.startsWith('cams:')) camsFiles.push(source.path);
  }
  if (!modelFiles.length || !camsFiles.length) throw new Error('Native regional inputs are incomplete');
  const frames = payload.frame_data as Record<string, unknown>;
  if (
    !frames || frames.version !== 103 || frames.encoding !== 'base64-quantized-row-major-v1'
    || frames.point_count !== count || !Array.isArray(frames.frames) || frames.frames.length !== 20
  ) {
    throw new Error('Native regional frames are incomplete');
  }
  const precision = requireExactObject(
    frames.precision, REGIONAL_PRECISION_FIELDS, 'Native regional precision',
  );
  requireNonEmptyString(precision.mode, 'Native regional precision mode');
  requireNonEmptyString(precision.aerosol_profile_mode, 'Native regional aerosol mode');
  const publicPrecision = requireExactObject(
    payload.precision, REGIONAL_PRECISION_FIELDS, 'Native regional public precision',
  );
  for (const field of ['point_spacing_deg', 'model_grid_spacing_deg']) {
    if (requireFiniteNumber(precision[field], `Native regional ${field}`) <= 0) {
      throw new Error(`Native regional ${field} must be positive`);
    }
  }
  if (
    precision.point_count !== count || precision.full_point_coverage !== true
    || typeof precision.vertical_profiles !== 'boolean'
    || requireNonNegativeInteger(precision.processed_sample_count, 'Native processed samples') > count * 20
    || requireNonNegativeInteger(precision.candidate_limit_per_sample, 'Native candidate limit') !== 0
    || REGIONAL_PRECISION_FIELDS.some((field) => publicPrecision[field] !== precision[field])
  ) throw new Error('Native regional precision is inconsistent');
  frames.frames.forEach((raw, index) => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error('Native regional frame is invalid');
    }
    const frame = raw as Record<string, unknown>;
    if (
      frame.offset_minutes !== (payload.phenomenon === 'dawn' ? -75 : -20) + index * 5
      || typeof frame.valid !== 'string' || !frame.valid
    ) throw new Error('Native regional frame identity is invalid');
  });
  const performance = frames.performance as Record<string, unknown>;
  const rawTiming = payload.timing as Record<string, unknown>;
  const ms = rawTiming?.native_timings_ms as Record<string, unknown>;
  const metric = (source: Record<string, unknown> | undefined, name: string) => {
    const value = source?.[name];
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
      throw new Error(`Native regional timing ${name} is invalid`);
    }
    return value;
  };
  const prefetch = metric(rawTiming, 'prefetch_seconds');
  const tensor = ['model_profiles_and_solar', 'model_decode_and_source_hash', 'model_pack_and_derive', 'cams_assembly']
    .reduce((sum, field) => sum + metric(ms, field), 0) / 1000;
  const compute = metric(ms, 'native_assessment') / 1000;
  const frameSeconds = metric(performance, 'total_seconds');
  const components: Record<string, string> =
    Object.fromEntries(COMPACT_POINT_FIELDS.slice(11, 24).map((field) => [field, field]));
  components.burnable_duration_minutes = 'duration_minutes';
  return {
    ...payload,
    algorithm: native.contract,
    point_format: 'native-object-v1', point_fields: [...COMPACT_POINT_FIELDS],
    data_provenance: {
      model_source: payload.model_source, cycle_utc: payload.cycle_time_utc,
      forecast_hours: [...new Set(hours)].sort((a, b) => a - b),
      model_files: modelFiles, cams_files: camsFiles,
    },
    timing: {
      prefetch_seconds: prefetch, tensor_seconds: tensor, compute_seconds: compute,
      frames_seconds: frameSeconds,
      frame_instant_scoring_seconds: metric(performance, 'instant_scoring_seconds'),
      frame_assembly_seconds: metric(performance, 'assembly_seconds'),
      frame_points_per_second: metric(performance, 'points_per_second'),
      frame_samples_per_second: metric(performance, 'samples_per_second'),
      total_seconds: prefetch + tensor + compute + frameSeconds,
    },
    points: points.map((raw) => {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        throw new Error('Native regional point is invalid');
      }
      const point = raw as Record<string, unknown>;
      const quality = point.quality_components as Record<string, unknown>;
      if (!quality || typeof quality !== 'object') throw new Error('Native point quality is missing');
      return COMPACT_POINT_FIELDS.map((field) =>
        components[field] ? quality[components[field]] : point[field]);
    }),
  };
}

/** Validate legacy array-v6 and independently versioned native regional results. */
export function normalizeRegionalForecastPayload(payload: RegionalForecastPayload): RegionalForecastPayload {
  const raw = payload as unknown as Record<string, unknown>;
  const native = raw.native_forecast as Record<string, unknown> | undefined;
  const source = raw.model_source;
  const compact = native?.contract === 'rust-desktop-regional-forecast-v1'
    ? nativeRegionalPointArrays(raw) : source === 'ec'
    ? validateExternalRegionalSummary(payload)
    : validateRegionalSummary(payload);
  if (compact.point_format !== 'array-v6' && compact.point_format !== 'native-object-v1') {
    throw new Error(`Unsupported regional point format: ${String(compact.point_format)}`);
  }
  if (
    !Array.isArray(compact.point_fields)
    || compact.point_fields.length !== COMPACT_POINT_FIELDS.length
    || compact.point_fields.some((field, index) => field !== COMPACT_POINT_FIELDS[index])
  ) {
    throw new Error('Regional point_fields does not match the array-v6 contract');
  }
  if (!Array.isArray(compact.points)) {
    throw new Error('Regional array-v6 points must be an array');
  }

  const fields = compact.point_fields;
  const points = compact.points.map((row, rowIndex) => {
    if (!Array.isArray(row) || row.length !== fields.length) {
      throw new Error(`Regional array-v6 point row ${rowIndex} has an invalid shape`);
    }
    const point: Record<string, unknown> = {};
    fields.forEach((field, index) => {
      point[field] = row[index];
    });
    if (typeof point.name !== 'string' || point.name.length === 0) {
      throw new Error(`Regional array-v6 point row ${rowIndex} has no valid name`);
    }
    if (
      typeof point.quality_level !== 'string'
      || point.quality_level.length === 0
      || typeof point.peak_quality_level !== 'string'
      || point.peak_quality_level.length === 0
    ) {
      throw new Error(`Regional array-v6 point row ${rowIndex} has invalid quality labels`);
    }
    if (
      point.dominant_burnable_pressure_family !== null
      && point.dominant_burnable_pressure_family !== 'high'
      && point.dominant_burnable_pressure_family !== 'middle'
      && point.dominant_burnable_pressure_family !== 'low'
    ) {
      throw new Error(`Regional array-v6 point row ${rowIndex} has invalid pressure family`);
    }
    if (point.best_time_local !== null) {
      try {
        requireStrictZonedIsoTime(
          point.best_time_local,
          `Regional array-v6 point row ${rowIndex}.best_time_local`,
        );
      } catch {
        throw new Error(`Regional array-v6 point row ${rowIndex} has invalid best_time_local`);
      }
    }
    const requiredFiniteFields = [
      'lat',
      'lon',
      'observer_elevation_asl_m',
      'quality_index',
      'peak_quality_index',
      'confidence_score',
      'burnable_cloud_coverage_effective',
      'forward_view_cloud_field_amount_peak',
      'forward_burnable_cloud_field_amount_peak',
      'forward_burnable_cloud_ratio_peak',
      'burnable_duration_minutes',
      'local_aod550',
      'quality_aod550',
      'incoming_aod_illumination_factor',
      'observer_aod_contrast_factor',
      'observer_return_optical_depth_mean',
      'observer_return_transmission_mean',
      'incoming_aod_adjustment',
      'observer_aod_adjustment',
      'high_cloud',
      'mid_cloud',
      'low_cloud',
    ] as const;
    for (const field of requiredFiniteFields) {
      if (typeof point[field] !== 'number' || !Number.isFinite(point[field])) {
        throw new Error(`Regional array-v6 point row ${rowIndex} has invalid ${field}`);
      }
    }
    const requirePointRange = (field: string, minimum: number, maximum: number) => {
      const value = point[field] as number;
      if (value < minimum || value > maximum) {
        throw new Error(`Regional array-v6 point row ${rowIndex} has out-of-range ${field}`);
      }
    };
    requirePointRange('lat', -90, 90);
    requirePointRange('lon', -180, 180);
    requirePointRange('observer_elevation_asl_m', -500, 9000);
    for (const field of ['quality_index', 'peak_quality_index']) {
      requirePointRange(field, 0, 2.5);
    }
    for (const field of [
      'confidence_score',
      'burnable_cloud_coverage_effective',
      'forward_view_cloud_field_amount_peak',
      'forward_burnable_cloud_field_amount_peak',
      'forward_burnable_cloud_ratio_peak',
      'incoming_aod_illumination_factor',
      'observer_aod_contrast_factor',
      'observer_return_transmission_mean',
      'high_cloud',
      'mid_cloud',
      'low_cloud',
    ]) {
      requirePointRange(field, 0, 1);
    }
    for (const field of [
      'burnable_duration_minutes',
      'local_aod550',
      'quality_aod550',
      'observer_return_optical_depth_mean',
    ]) {
      requirePointRange(field, 0, Number.POSITIVE_INFINITY);
    }
    requirePointRange('incoming_aod_adjustment', -2.5, 2.5);
    requirePointRange('observer_aod_adjustment', -2.5, 2.5);
    return point as unknown as RegionalForecastPoint;
  });

  return { ...compact, points } as unknown as RegionalForecastPayload;
}

/** POST synchronous forecast (blocks until complete) */
export function runForecast(body: ForecastRequest): Promise<ForecastPayload> {
  return post<ForecastPayload>('/api/v1/firesky/forecasts/compute', body).then((payload) => {
    if (payload.nowcast !== undefined && payload.nowcast !== null) {
      validateNowcastCloudContract(payload.nowcast);
    }
    return payload;
  });
}

/** Start async forecast, returns job_id for SSE polling */
export function startForecast(body: ForecastRequest): Promise<ForecastStartResponse> {
  return post<ForecastStartResponse>('/api/v1/firesky/forecasts', body);
}

/** POST synchronous nowcast (satellite-primary prediction) */
export function runNowcast(body: ForecastRequest): Promise<ForecastPayload> {
  return post<ForecastPayload>('/api/v1/firesky/forecasts/nowcast/compute', {
    include_map_cells: true, ...body,
  }).then((payload) => {
    if (payload.nowcast === undefined || payload.nowcast === null) {
      throw new Error('Nowcast response is missing the calibrated cloud-column contract');
    }
    validateNowcastCloudContract(payload.nowcast);
    return payload;
  });
}

/** Start async nowcast, returns job_id for SSE polling */
export function startNowcast(body: ForecastRequest): Promise<ForecastStartResponse> {
  return post<ForecastStartResponse>('/api/v1/firesky/forecasts/nowcast', {
    include_map_cells: true, ...body,
  });
}

export function runRegionalForecast(body: RegionalForecastRequest): Promise<RegionalForecastPayload> {
  return ensureRegionalForecastContract()
    .then(() => post<RegionalForecastPayload>('/api/v1/firesky/forecasts/national/compute', body))
    .then(normalizeRegionalForecastPayload);
}

export function startRegionalForecast(body: RegionalForecastRequest): Promise<RegionalForecastStartResponse> {
  return ensureRegionalForecastContract()
    .then(() => post<RegionalForecastStartResponse>('/api/v1/firesky/forecasts/national', body));
}

function requireCustomRegionQuery(body: RegionalForecastRequest): {
  bounds: RegionalBounds;
  timezoneName: string;
} | null {
  if (!body.regions.includes('custom')) return null;
  if (!body.custom_bounds) throw new Error('自定义区域缺少 custom_bounds。');
  const timezoneName = body.custom_timezone_name;
  if (typeof timezoneName !== 'string' || timezoneName.trim() !== timezoneName || !timezoneName) {
    throw new Error('自定义区域缺少有效的 IANA 时区。');
  }
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: timezoneName }).format(new Date(0));
  } catch {
    throw new Error('自定义区域缺少有效的 IANA 时区。');
  }
  return { bounds: body.custom_bounds, timezoneName };
}

export function getCachedRegionalForecast(
  body: RegionalForecastRequest,
  signal?: AbortSignal,
): Promise<RegionalForecastPayload> {
  const params: Record<string, string> = {
    target_date: body.target_date,
    phenomenon: body.phenomenon,
    model_source: body.model_source,
    forecast_mode: body.forecast_mode ?? 'model',
    satellite_source: body.satellite_source ?? 'off',
    spacing_deg: String(body.spacing_deg ?? 2),
    compute_backend: body.compute_backend ?? 'auto',
    gfs_resolution: body.gfs_resolution ?? '0p25',
    regions: body.regions.join(','),
  };
  const cycleOverrideKey = body.model_source === 'gfs' ? 'gfs_cycle_override' : 'ec_cycle_override';
  const cycleOverride = body.model_source === 'gfs'
    ? body._gfs_cycle_override
    : body._ec_cycle_override;
  if (cycleOverride) params[cycleOverrideKey] = cycleOverride;
  const custom = requireCustomRegionQuery(body);
  if (custom) {
    params.custom_west = String(custom.bounds.west);
    params.custom_south = String(custom.bounds.south);
    params.custom_east = String(custom.bounds.east);
    params.custom_north = String(custom.bounds.north);
    params.custom_label = body.custom_label ?? '自定义区域';
    params.custom_timezone_name = custom.timezoneName;
  }
  return ensureRegionalForecastContract(signal)
    .then(() => get<RegionalForecastPayload>('/api/v1/firesky/forecasts/national/summary', params, signal))
    .then(normalizeRegionalForecastPayload);
}

export function getRegionalDiagnostic(
  token: string,
  lat: number,
  lon: number,
): Promise<RegionalForecastDiagnosticResponse> {
  return get<RegionalForecastDiagnosticResponse>('/api/v1/firesky/forecasts/national/diagnostic', {
    token,
    lat: String(lat),
    lon: String(lon),
  });
}

export function getRegionalDiagnosticByJob(
  jobId: string,
  lat: number,
  lon: number,
): Promise<RegionalForecastDiagnosticResponse> {
  return get<RegionalForecastDiagnosticResponse>(`/api/v1/firesky/forecast/jobs/${encodeURIComponent(jobId)}/diagnostic`, {
    lat: String(lat),
    lon: String(lon),
  });
}

export function getNationalCrossSection(
  body: NationalCrossSectionRequest,
  lat: number,
  lon: number,
  offsetMinutes: number,
): Promise<NationalCrossSectionPayload> {
  if (!Number.isFinite(body.elevation_m) || body.elevation_m < -500 || body.elevation_m > 9000) {
    throw new Error('全国剖面请求缺少有效的观测者海拔。');
  }
  const params: Record<string, string> = {
    target_date: body.target_date,
    phenomenon: body.phenomenon,
    model_source: body.model_source,
    forecast_mode: body.forecast_mode ?? 'model',
    satellite_source: body.satellite_source ?? 'off',
    spacing_deg: String(body.spacing_deg ?? 2),
    gfs_resolution: body.gfs_resolution ?? '0p25',
    regions: body.regions.join(','),
    elevation_m: String(body.elevation_m),
    lat: String(lat),
    lon: String(lon),
    offset_minutes: String(offsetMinutes),
  };
  if (body.profile_key) params.profile_key = body.profile_key;
  const custom = requireCustomRegionQuery(body);
  if (custom) {
    params.custom_west = String(custom.bounds.west);
    params.custom_south = String(custom.bounds.south);
    params.custom_east = String(custom.bounds.east);
    params.custom_north = String(custom.bounds.north);
    params.custom_label = body.custom_label ?? '自定义区域';
    params.custom_timezone_name = custom.timezoneName;
  }
  return get<NationalCrossSectionPayload>('/api/v1/firesky/forecasts/national/cross-section', params);
}
