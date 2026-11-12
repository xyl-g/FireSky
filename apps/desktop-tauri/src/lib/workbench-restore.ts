import type { ForecastKind, ObservationSpot } from "./workbench-types";
import { validCustomRegionBounds, type RegionBounds } from "./map-region";

export interface RestoredRequest {
  spot: ObservationSpot;
  targetDate: string;
  phenomenon: "dawn" | "dusk";
  regions: string[];
  customRegion: boolean;
  bounds: { west: number; east: number; south: number; north: number } | null;
}
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

/** Validate a history snapshot atomically before changing any draft form fields. */
export function parseRestoredRequest(nextKind: ForecastKind, payload: Record<string, unknown>): RestoredRequest | null {
  if (!["point", "regional", "nowcast"].includes(nextKind)
    || typeof payload.target_date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(payload.target_date)
    || !Number.isFinite(Date.parse(`${payload.target_date}T12:00:00Z`))
    || new Date(`${payload.target_date}T12:00:00Z`).toISOString().slice(0, 10) !== payload.target_date
    || !["dawn", "dusk"].includes(String(payload.phenomenon))
    || typeof payload.name !== "string" || !payload.name.trim()
    || !finite(payload.lat) || Math.abs(payload.lat) > 90 || !finite(payload.lon) || Math.abs(payload.lon) > 180
    || !finite(payload.elevation_m) || (nextKind !== "regional" && payload.elevation_m === -1)
    || typeof payload.timezone_name !== "string") return null;
  try { new Intl.DateTimeFormat("en", { timeZone: payload.timezone_name }); } catch { return null; }
  let regions: string[] = [], bounds: RestoredRequest["bounds"] = null;
  if (nextKind === "regional") {
    if (!Array.isArray(payload.regions) || !payload.regions.length || payload.regions.some((value) => typeof value !== "string" || !value)) return null;
    regions = [...new Set(payload.regions as string[])];
    if (regions.includes("custom")) {
      const raw = payload.custom_bounds;
      if (!object(raw) || !finite(raw.west) || !finite(raw.east) || !finite(raw.south) || !finite(raw.north)) return null;
      const west = raw.west;
      const east = raw.east;
      const south = raw.south;
      const north = raw.north;
      const candidate: RegionBounds = { west, east, south, north };
      if (!validCustomRegionBounds(candidate)) return null;
      bounds = candidate;
    }
  }
  return {
    spot: { name: payload.name, lat: payload.lat, lon: payload.lon, elevation_m: payload.elevation_m, timezone_name: payload.timezone_name, metadataReady: payload.elevation_m !== -1 },
    targetDate: payload.target_date, phenomenon: payload.phenomenon as "dawn" | "dusk",
    regions: regions.filter((code) => code !== "custom"), customRegion: regions.includes("custom"), bounds,
  };
}

export function restoredRequest(currentKind: ForecastKind, nextKind: ForecastKind, payload: Record<string, unknown>): RestoredRequest | null {
  return currentKind === nextKind ? parseRestoredRequest(nextKind, payload) : null;
}
