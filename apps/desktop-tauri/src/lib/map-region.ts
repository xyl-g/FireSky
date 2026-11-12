export interface RegionBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

function validBounds(
  value: RegionBounds | null | undefined,
  minLatitude: number,
  maxLatitude: number,
): value is RegionBounds {
  return !!value && [value.west, value.south, value.east, value.north].every(Number.isFinite)
    && value.west >= -180 && value.west <= 180 && value.east >= -180 && value.east <= 180
    && value.south >= minLatitude && value.north <= maxLatitude
    && value.west !== value.east && value.north > value.south;
}

export function validRegionBounds(value: RegionBounds | null | undefined): value is RegionBounds {
  return validBounds(value, -85, 85);
}

export function validCustomRegionBounds(value: RegionBounds | null | undefined): value is RegionBounds {
  return validBounds(value, -90, 90);
}

/** Convert a wrapped date-line interval into ordinary Leaflet rectangles. */
export function splitRegionBounds(value: RegionBounds): RegionBounds[] {
  if (!validRegionBounds(value)) return [];
  if (value.west < value.east) return [{ ...value }];
  return [
    { west: value.west, south: value.south, east: 180, north: value.north },
    { west: -180, south: value.south, east: value.east, north: value.north },
  ].filter((part) => part.west < part.east);
}

/** Match Swift GridBounds.drawn: expand the rectangle to the 0.25-degree grid. */
export function drawnRegionBounds(value: RegionBounds): RegionBounds | null {
  if (!validCustomRegionBounds(value)) return null;
  const bounds = {
    west: Math.max(-180, Math.floor(value.west * 4) / 4),
    south: Math.max(-85, Math.floor(value.south * 4) / 4),
    east: Math.min(180, Math.ceil(value.east * 4) / 4),
    north: Math.min(85, Math.ceil(value.north * 4) / 4),
  };
  return validRegionBounds(bounds) ? bounds : null;
}
