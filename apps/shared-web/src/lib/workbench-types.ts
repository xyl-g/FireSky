import type { BaseId, SourceId } from "./map-grid";

export type ForecastKind = "point" | "regional" | "nowcast";
export interface ObservationSpot {
  name: string;
  lat: number;
  lon: number;
  elevation_m: number;
  timezone_name: string;
  metadataReady: boolean;
  /** 引擎 metadata/search 响应的 source 字段（open-meteo-point-metadata / local-timezone+srtm），仅展示用。 */
  metadataSource?: string;
}
export interface MapOptions {
  provider: SourceId;
  tdtKey: string;
  base: BaseId;
  dark: boolean;
}
