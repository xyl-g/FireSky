type Row = Record<string, unknown>;
export interface RegionalDetail { label: string; value: string }

function record(value: unknown): Row {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}
function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function fixed(value: unknown, digits: number, suffix = "", scale = 1): string {
  const result = number(value);
  return result === null ? "--" : `${(result * scale).toFixed(digits)}${suffix}`;
}
function signed(value: unknown): string {
  const result = number(value);
  return result === null ? "--" : `${result >= 0 ? "+" : ""}${result.toFixed(3)}`;
}
function pressure(value: unknown): string {
  if (value === null) return "无可烧目标云";
  const labels: Row = { high: "高云压力族", middle: "中云压力族", low: "低云压力族" };
  return typeof value === "string" && typeof labels[value] === "string" ? String(labels[value]) : "--";
}

export function regionalPointDetails(value: unknown, valid: boolean): RegionalDetail[] {
  const row = valid ? record(value) : {};
  return [
    { label: "事件窗口质量", value: fixed(row.quality_index, 3) },
    { label: "主导可烧压力族", value: pressure(row.dominant_burnable_pressure_family) },
    { label: "有效可烧云", value: fixed(row.burnable_cloud_coverage_effective, 1, "%", 100) },
    { label: "可烧时长", value: fixed(row.burnable_duration_minutes, 0, " 分钟") },
    { label: "质量 AOD", value: fixed(row.quality_aod550, 3) },
    { label: "入射修正", value: signed(row.incoming_aod_adjustment) },
    { label: "观测修正", value: signed(row.observer_aod_adjustment) },
    { label: "置信度", value: fixed(row.confidence_score, 0, "%", 100) },
  ];
}

function times(value: unknown): string {
  const entries = Array.isArray(value) ? value : [value];
  const valid = entries.filter((entry): entry is string => typeof entry === "string" && entry.length > 0);
  return valid.length ? [...new Set(valid)].map((entry) => entry.replace("T", " ")).join(" · ") : "未提供";
}
function boolean(value: unknown): string {
  return value === true ? "是" : value === false ? "否" : "未提供";
}
function positive(value: unknown): number | null {
  const result = number(value);
  return result !== null && result > 0 ? result : null;
}

export function regionalSpacing(value: unknown): number | null {
  return positive(record(record(value).precision).point_spacing_deg);
}
export function regionalSpacingLabel(value: unknown): string {
  const spacing = regionalSpacing(value);
  return spacing === null ? "网格精度未提供" : `${spacing}° × ${spacing}°`;
}

export function regionalReportDetails(value: unknown): RegionalDetail[] {
  const report = record(value), provenance = record(report.data_provenance), precision = record(report.precision);
  const manifest = record(report.input_manifest);
  const sources = Array.isArray(manifest.sources) ? manifest.sources : [];
  // Native receipts identify consumed CAMS valid times, not the run that produced them.
  const receiptCamsTimes = sources.map((source) => record(source).role)
    .filter((role): role is string => typeof role === "string" && role.startsWith("cams:"))
    .map((role) => role.slice(5));
  const hours = provenance.gfs_forecast_hours ?? provenance.forecast_hours;
  const forecastHours = Array.isArray(hours) ? hours.filter((hour) => number(hour) !== null).map((hour) => `${hour}h`).join(" · ") : "";
  return [
    { label: "模式起报 UTC", value: times(provenance.served_gfs_cycle_utc ?? provenance.cycle_utc ?? report.cycle_time_utc) },
    { label: "模式预报时效", value: forecastHours || "未提供" },
    { label: "CAMS 起报 UTC", value: times(provenance.cams_run_times_utc) },
    { label: "CAMS 有效时次 UTC", value: times(provenance.cams_valid_times_utc ?? receiptCamsTimes) },
    { label: "采样网格", value: regionalSpacingLabel(report) },
    { label: "模式网格", value: positive(precision.model_grid_spacing_deg) === null ? "未提供" : `${precision.model_grid_spacing_deg}°` },
    { label: "垂直剖面", value: boolean(precision.vertical_profiles) },
    { label: "完整点覆盖", value: boolean(precision.full_point_coverage) },
    { label: "气溶胶剖面", value: typeof precision.aerosol_profile_mode === "string" ? precision.aerosol_profile_mode : "未提供" },
  ];
}
