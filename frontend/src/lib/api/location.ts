import { get } from './client';
import type { LocationResult, PointResult } from './types';

/** Geocoding / location search */
export function searchLocation(query: string): Promise<LocationResult> {
  return get<LocationResult>('/api/v1/firesky/locations/search', { query });
}

/** Get timezone + elevation for a coordinate */
export function getPoint(lat: number, lon: number): Promise<PointResult> {
  return get<PointResult>('/api/v1/firesky/points/metadata', {
    lat: lat.toString(),
    lon: lon.toString(),
  });
}
