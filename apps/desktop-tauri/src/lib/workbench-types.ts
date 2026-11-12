import type { BaseId, SourceId } from "./map-grid";

export type ForecastKind = "point" | "regional" | "nowcast";
export interface ObservationSpot {
  name: string;
  lat: number;
  lon: number;
  elevation_m: number;
  timezone_name: string;
  metadataReady: boolean;
}
export interface MapOptions {
  provider: SourceId;
  tdtKey: string;
  base: BaseId;
  dark: boolean;
}
