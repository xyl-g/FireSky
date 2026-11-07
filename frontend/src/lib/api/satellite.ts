import { get } from './client';
import type {
  SatelliteFramesResponse,
  SatelliteMotionResponse,
} from './types';

/** Get available satellite frames for a given observation point */
export function getSatelliteFrames(lat: number, lon: number): Promise<SatelliteFramesResponse> {
  return get<SatelliteFramesResponse>('/api/v1/firesky/satellite/frames', {
    lat: lat.toString(),
    lon: lon.toString(),
  });
}

/** Get satellite image URL for a specific frame (for use with L.imageOverlay) */
export function getSatelliteImageUrl(
  area: string,
  channel: string,
  token: string,
): string {
  const params = new URLSearchParams({ area, channel, token });
  return `/api/v1/firesky/satellite/image?${params.toString()}`;
}

/** Get cloud motion vectors between recent frames */
export function getSatelliteMotion(lat: number, lon: number): Promise<SatelliteMotionResponse> {
  return get<SatelliteMotionResponse>('/api/v1/firesky/satellite/motion', {
    lat: lat.toString(),
    lon: lon.toString(),
  });
}
