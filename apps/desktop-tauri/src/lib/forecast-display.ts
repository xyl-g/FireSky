import type { GridCell } from "./map-grid";

export type RecordValue = Record<string, unknown>;
export const record = (value: unknown): RecordValue =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
export const numeric = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
export const format = (value: unknown, digits = 2): string => numeric(value)?.toFixed(digits) ?? "--";
export const percent = (value: unknown): string => numeric(value) === null ? "--" : `${(Number(value) * 100).toFixed(0)}%`;
export const timeLabel = (value: unknown): string => typeof value === "string" && value.length >= 16 ? value.slice(11, 16) : "--";
export function modelReport(result: RecordValue | null, model: string): RecordValue | null {
  if (record(result?.model_errors)[model] != null) return null;
  const value = record(result?.model_results)[model];
  return value && typeof value === "object" ? record(value) : null;
}
export function modelError(result: RecordValue | null, model: string): string {
  const value = record(result?.model_errors)[model];
  return typeof value === "string" ? value : "";
}
export function windowLabel(report: RecordValue | null): string {
  const window = record(report?.timing_window);
  const geometry = record(report?.geometry_window), samples = steps(report);
  return `${timeLabel(window.start_time_local ?? geometry.start_time_local ?? samples[0]?.valid_time_local)} - ${timeLabel(window.end_time_local ?? geometry.end_time_local ?? samples.at(-1)?.valid_time_local)}`;
}
export const metrics = [
  ["burnable", "有效可烧云量", "前向可烧云占视野比例"],
  ["material", "云质厚度", "云体材质负载"],
  ["brightness", "亮度色彩", "霞光亮度与色彩评分"],
  ["aod", "AOD 550", "事件时刻 · 气溶胶光学厚度"],
  ["incoming", "入射受光", "太阳入射照亮云体的因子"],
  ["contrast", "返程对比度", "观测者视线大气对比因子"],
  ["duration", "持续时长", "核心观赏窗口 · 分钟"],
  ["confidence", "置信度", "引擎结果置信评分"],
  ["disagreement", "模式分歧", "多模式结论分歧度"],
];
export const nowcastMetrics = [
  ["near_cloud_ratio", "近场云量", "观测点附近卫星云覆盖"],
  ["far_cloud_ratio", "远场云量", "太阳方向远场云覆盖"],
  ["satellite_low_cloud_state", "低云状态", "卫星校准与垂直先验"],
  ["satellite_motion_quality", "运动质量", "卫星云体运动状态"],
  ["convective_score_ceiling", "对流顶棚", "对流约束评分上限"],
  ["geometric_path_blocking_risk", "光路阻塞风险", "太阳入射路径"],
  ["calibrated_sample_coverage_ratio", "采样覆盖率", "卫星有效采样比例"],
  ["confidence", "实况置信度", "卫星反演置信评分"],
  ["aod", "AOD 550", "事件时刻 · 气溶胶光学厚度"],
];
export function metricValue(key: string, report: RecordValue | null, nowcast = false): string {
  if (!report) return "--";
  if (key === "aod") return format(record(report.aerosol).aod550);
  if (key === "confidence") return percent(nowcast ? record(report.nowcast).confidence : report.confidence_score);
  const components = record(report.quality_components);
  if (nowcast) {
    const value = record(report.nowcast)[key];
    if (key === "satellite_motion_quality") return ({ observation: "原始观测", strong: "可靠", decayed: "已衰减", unknown: "未知", "diagnostic-only": "仅供诊断" } as Record<string, string>)[String(value)] ?? (typeof value === "string" ? value : "--");
    if (key === "satellite_low_cloud_state") return ({ detected: "检出低云", clear: "未检出低云", unknown: "未知", supported: "支持低云", not_supported: "不支持低云" } as Record<string, string>)[String(value)] ?? "--";
    return percent(value);
  }
  switch (key) {
    case "burnable": return percent(components.burnable_cloud_coverage_effective ?? report.burnable_cloud_coverage_effective);
    case "material": return format(components.cloud_material);
    case "brightness": return format(components.brightness_color);
    case "incoming": return format(components.incoming_aod_illumination_factor);
    case "contrast": return format(components.observer_aod_contrast_factor);
    case "duration": return format(report.duration_minutes ?? components.duration_minutes, 0);
    case "confidence": return percent(report.confidence_score);
    case "disagreement": return format(report.model_disagreement_score);
    default: return "--";
  }
}
export function steps(report: RecordValue | null): RecordValue[] {
  return Array.isArray(report?.steps) ? report.steps.map(record) : [];
}
/** Preserve invalid samples as gaps; do not connect a curve across missing data. */
export function curveSegments(report: RecordValue | null): Array<Array<{ time: number; value: number }>> {
  const segments: Array<Array<{ time: number; value: number }>> = [[]];
  const seen = new Set<number>();
  for (const step of steps(report)) {
    // The Python report emits offset-aware local times; never parse naive times in the browser's zone.
    const stamp = step.valid_time_utc ?? step.valid_time_local;
    const time = typeof stamp === "string" && /(?:z|[+-]\d{2}:\d{2})$/i.test(stamp) ? Date.parse(stamp) : NaN;
    const value = numeric(step.instant_quality_index);
    if (!Number.isFinite(time) || value === null || step.valid === false) {
      if (segments.at(-1)!.length) segments.push([]);
      continue;
    }
    if (seen.has(time)) continue;
    seen.add(time);
    segments.at(-1)!.push({ time, value });
  }
  return segments.filter((segment) => segment.length).map((segment) => segment.sort((a, b) => a.time - b.time));
}
export function regionalCells(report: RecordValue | null): GridCell[] {
  const cells = new Map<string, GridCell>();
  for (const [rows, failed] of [[report?.points, false], [report?.errors, true]] as const) {
    if (!Array.isArray(rows)) continue;
    for (const item of rows) {
      const row = record(item), lat = numeric(row.lat), lon = numeric(row.lon);
      if (lat === null || lon === null || Math.abs(lat) > 90 || Math.abs(lon) > 180) continue;
      const value = failed ? null : numeric(row.peak_quality_index ?? row.quality_index);
      cells.set(`${lat},${lon}`, [lat, lon, value !== null && value >= 0 && value <= 2.5 ? value : null]);
    }
  }
  return [...cells.values()];
}
