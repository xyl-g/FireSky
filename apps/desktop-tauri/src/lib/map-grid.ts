/** Pure grid sanitation/inspection shared by the canvas and offline Node tests. */
export type GridCell = readonly [lat: number, lon: number, value: number | null];
export interface MapSpot { lat: number; lon: number; name?: string }
export type SourceId = "openfreemap" | "tianditu";
export type BaseId = "imagery" | "vector" | "terrain";
export interface MapViewError {
  kind: "initialization" | "tiles" | "grid";
  message: string;
  count?: number;
}

function numeric(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function validCoordinates(lat: number, lon: number): boolean {
  return Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
}

/** Accept the legacy [lat, lon, value] API or Python-style point records.
 * Zero is a measurement, not absence. Non-finite/absent values stay missing;
 * invalid coordinates are discarded. Never interpolate, round or limit cells.
 */
export function sanitizeGrid(input: unknown): GridCell[] {
  if (!Array.isArray(input)) return [];
  const cells: GridCell[] = [];
  for (const item of input) {
    if (!item || typeof item !== "object") continue;
    const lat = numeric(Array.isArray(item) ? item[0] : item.lat);
    const lon = numeric(Array.isArray(item) ? item[1] : item.lon);
    const value = numeric(Array.isArray(item) ? item[2] : item.peak_quality_index);
    if (lat === null || lon === null || !validCoordinates(lat, lon)) continue;
    cells.push([lat, lon, value]);
  }
  return cells;
}

export function gridSpacing(value: number): number {
  return Number.isFinite(value) && value > 0 && value <= 180 ? value : 0.25;
}

export function gridColor(value: number | null): string {
  if (value === null) return "#5c6470";
  if (value < 0.05) return "#87929e";
  if (value < 0.4) return "#d6b56a";
  if (value < 1) return "#ea7c2c";
  return "#c0261b";
}

export function findGridCell(cells: readonly GridCell[], spacing: number, lat: number, lon: number): GridCell | undefined {
  if (!validCoordinates(lat, lon)) return undefined;
  const half = gridSpacing(spacing) / 2;
  return cells.find((cell) => Math.abs(cell[0] - lat) <= half && Math.abs(cell[1] - lon) <= half);
}

export function gridCellText(cell: GridCell): string {
  return `${cell[0].toFixed(4)}, ${cell[1].toFixed(4)} · ${cell[2] === null ? "缺测 / 计算失败" : `峰值 ${cell[2].toFixed(3)}`}`;
}

export function gridBounds(cells: readonly GridCell[], spacing: number): [[number, number], [number, number]] | null {
  if (!cells.length) return null;
  let south = Infinity, north = -Infinity, west = Infinity, east = -Infinity;
  for (const [lat, lon] of cells) {
    south = Math.min(south, lat); north = Math.max(north, lat);
    west = Math.min(west, lon); east = Math.max(east, lon);
  }
  const half = gridSpacing(spacing) / 2;
  // A normal Leaflet bounds would turn a wrapped grid into an almost-global view.
  if (east - west + half * 2 > 180) return null;
  const mercatorLat = (lat: number) => Math.max(-85.05, Math.min(85.05, lat));
  return [[mercatorLat(south - half), west - half], [mercatorLat(north + half), east + half]];
}

/** Development-only caller fixture; synthetic, not a forecast. */
export const DEVELOPMENT_GRID: readonly GridCell[] = [
  [30.25, 120, 0.72],
  [30.25, 120.25, 0],
  [30.25, 120.5, null],
];
