export interface InventoryCycle {
  label?: string;
  files: number;
  mb: number;
  hours: number[];
  coverage?: Record<string, Array<number[] | null>>;
  area_keys?: Record<string, string[]>;
}

export interface InventoryDataset {
  source: string;
  path: string;
  total_file_count: number;
  total_storage_mb: number;
  unindexed_file_count: number;
  latest_cycle: string | null;
  cycles: Record<string, InventoryCycle>;
  area_bounds?: Record<string, number[] | null>;
}

export interface ProbeRow {
  source: string;
  title: string;
  host?: string;
  state: string;
  detail: string;
  bytes_received?: number;
  elapsed_ms?: number;
}

export const sourceTitle = (source: string) => source === "ecmwf" || source === "ec" ? "EC" : source.toUpperCase();

export function formatMB(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "未知";
  return value >= 1024 ? `${(value / 1024).toFixed(2)} GB` : `${value.toFixed(1)} MB`;
}

export function localDate(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

export function cycleCoverage(dataset: InventoryDataset, cycle: InventoryCycle): string {
  if (dataset.source === "ecmwf") return "全球原始场";
  const areas = new Set<string>();
  if (dataset.source === "gfs") {
    for (const boxes of Object.values(cycle.coverage ?? {})) {
      for (const box of boxes) areas.add(box === null ? "全球" : `W${box[0]} E${box[1]} N${box[2]} S${box[3]}`);
    }
  } else {
    for (const keys of Object.values(cycle.area_keys ?? {})) {
      for (const key of keys) {
        const box = dataset.area_bounds?.[key];
        areas.add(box ? `N${box[0]} W${box[1]} S${box[2]} E${box[3]}` : `范围未识别 (${key})`);
      }
    }
  }
  return [...areas].sort().join(" / ") || "范围未识别";
}

export function initialProbes(): ProbeRow[] {
  return [
    { source: "gfs", title: "GFS · NOAA NOMADS", state: "idle", detail: "尚未探测" },
    { source: "ec", title: "EC · ECMWF Open Data", state: "idle", detail: "尚未探测" },
    { source: "cams", title: "CAMS · Copernicus ADS", state: "idle", detail: "尚未探测" },
  ];
}

export function probeLabel(state: string): string {
  if (state === "idle") return "未探测";
  if (state === "testing") return "探测中";
  if (state === "passed") return "下载解码通过";
  if (state === "cancelled") return "已取消";
  if (state === "not_configured") return "未配置凭据";
  if (state === "timeout") return "超时";
  return "未通过";
}
