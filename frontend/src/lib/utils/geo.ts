/**
 * Geographic utility functions
 * Ported from map.html destinationPoint()
 */

const EARTH_RADIUS_KM = 6371;

/**
 * Calculate a destination point given start, bearing, and distance.
 * Returns [lat, lon] in degrees.
 */
export function destinationPoint(
  lat: number,
  lon: number,
  bearingDeg: number,
  distanceKm: number,
): [number, number] {
  const d = distanceKm / EARTH_RADIUS_KM;
  const b = (bearingDeg * Math.PI) / 180;
  const p1 = (lat * Math.PI) / 180;
  const l1 = (lon * Math.PI) / 180;

  const p2 = Math.asin(
    Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b),
  );
  const l2 =
    l1 +
    Math.atan2(
      Math.sin(b) * Math.sin(d) * Math.cos(p1),
      Math.cos(d) - Math.sin(p1) * Math.sin(p2),
    );

  return [(p2 * 180) / Math.PI, ((((l2 * 180) / Math.PI) + 540) % 360) - 180];
}

/**
 * Calculate grid step size from a set of cell values.
 * Returns the median step, or fallback if insufficient data.
 */
export function gridStep(
  cells: { lat: number; lon: number }[],
  key: 'lat' | 'lon',
  fallback: number = 0.25,
): number {
  const values = [
    ...new Set(cells.map((c) => Number(c[key]).toFixed(5))),
  ]
    .map(Number)
    .sort((a, b) => a - b);

  const steps: number[] = [];
  for (let i = 1; i < values.length; i++) {
    const step = values[i] - values[i - 1];
    if (step > 0.00001) steps.push(step);
  }
  steps.sort((a, b) => a - b);

  return steps.length ? steps[Math.floor(steps.length / 2)] : fallback;
}
