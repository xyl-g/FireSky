import { get } from './client';
import { requireStrictZonedIsoTime } from './date-contract';
import type { MapFramePayload } from './types';

const MAP_FRAME_FIELDS = [
  'valid_time_local',
  'valid',
  'solar_elevation_deg',
  'solar_azimuth_deg',
  'instant_quality_index',
  'physically_burnable',
  'burnable_cloud_coverage',
  'forward_view_cloud_field_amount',
  'forward_burnable_cloud_field_amount',
  'forward_burnable_cloud_load',
  'forward_burnable_cloud_material_load',
  'forward_burnable_cloud_incoming_aerosol_load',
  'solar_incoming_block_risk',
  'forward_burnable_cloud_ratio',
  'observer',
  'cells',
] as const;
const MAP_FRAME_OBSERVER_FIELDS = ['lat', 'lon'] as const;
const MAP_FRAME_CELL_FIELDS = [
  'lat',
  'lon',
  'distance_km',
  'bearing_deg',
  'high_cloud',
  'mid_cloud',
  'low_cloud',
  'cloud_evidence',
  'deep_cloud_risk',
  'burn_score',
  'illumination_score',
  'path_clearance_score',
  'blocking_risk',
] as const;
function exactObject(
  value: unknown,
  fields: readonly string[],
  label: string,
): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  const result = value as Record<string, unknown>;
  if (
    Object.keys(result).length !== fields.length
    || fields.some((field) => !Object.prototype.hasOwnProperty.call(result, field))
  ) {
    throw new Error(`${label} fields do not match map-frame-v23`);
  }
  return result;
}

function rangedNumber(
  value: unknown,
  label: string,
  minimum: number,
  maximum: number,
  maximumInclusive = true,
): number {
  if (
    typeof value !== 'number'
    || !Number.isFinite(value)
    || value < minimum
    || (maximumInclusive ? value > maximum : value >= maximum)
  ) {
    throw new Error(`${label} is outside the map-frame-v23 range`);
  }
  return value;
}

export function validateMapFramePayload(payload: unknown): MapFramePayload {
  const frame = exactObject(payload, MAP_FRAME_FIELDS, 'Map frame');
  requireStrictZonedIsoTime(frame.valid_time_local, 'Map frame valid_time_local');
  if (typeof frame.valid !== 'boolean') {
    throw new Error('Map frame valid must be boolean');
  }
  rangedNumber(frame.solar_elevation_deg, 'Map frame solar_elevation_deg', -90, 90);
  rangedNumber(frame.solar_azimuth_deg, 'Map frame solar_azimuth_deg', 0, 360, false);
  rangedNumber(frame.instant_quality_index, 'Map frame instant_quality_index', 0, 2.5);
  if (typeof frame.physically_burnable !== 'boolean') {
    throw new Error('Map frame physically_burnable must be boolean');
  }
  for (const field of [
    'burnable_cloud_coverage',
    'forward_view_cloud_field_amount',
    'forward_burnable_cloud_field_amount',
    'solar_incoming_block_risk',
    'forward_burnable_cloud_ratio',
  ]) {
    rangedNumber(frame[field], `Map frame ${field}`, 0, 1);
  }
  for (const field of [
    'forward_burnable_cloud_load',
    'forward_burnable_cloud_material_load',
    'forward_burnable_cloud_incoming_aerosol_load',
  ]) {
    rangedNumber(frame[field], `Map frame ${field}`, 0, 2.5);
  }

  const observer = exactObject(frame.observer, MAP_FRAME_OBSERVER_FIELDS, 'Map frame observer');
  rangedNumber(observer.lat, 'Map frame observer.lat', -90, 90);
  rangedNumber(observer.lon, 'Map frame observer.lon', -180, 180);
  if (!Array.isArray(frame.cells)) {
    throw new Error('Map frame cells must be an array');
  }
  frame.cells.forEach((rawCell, index) => {
    const cell = exactObject(rawCell, MAP_FRAME_CELL_FIELDS, `Map frame cell ${index}`);
    rangedNumber(cell.lat, `Map frame cell ${index}.lat`, -90, 90);
    rangedNumber(cell.lon, `Map frame cell ${index}.lon`, -180, 180);
    rangedNumber(cell.distance_km, `Map frame cell ${index}.distance_km`, 0, Number.POSITIVE_INFINITY);
    rangedNumber(cell.bearing_deg, `Map frame cell ${index}.bearing_deg`, 0, 360, false);
    for (const field of [
      'high_cloud',
      'mid_cloud',
      'low_cloud',
      'cloud_evidence',
      'deep_cloud_risk',
      'burn_score',
      'illumination_score',
      'path_clearance_score',
      'blocking_risk',
    ]) {
      rangedNumber(cell[field], `Map frame cell ${index}.${field}`, 0, 1);
    }
  });
  return payload as MapFramePayload;
}

/** Fetch cloud field data for a specific time frame */
export function getMapFrame(token: string, time: string): Promise<MapFramePayload> {
  return get<unknown>('/api/v1/firesky/map-frames', { token, time })
    .then(validateMapFramePayload);
}
