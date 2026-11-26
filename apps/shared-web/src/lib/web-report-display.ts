import { format, numeric, percent, record, type RecordValue } from "./forecast-display";

export const familyLabels: Record<string, string> = { high: "高云压力族", middle: "中云压力族", low: "低云压力族" };
export const genusLabels: Record<string, string> = {
  cirrus: "卷云", cirrocumulus: "卷积云", cirrostratus: "卷层云", altocumulus: "高积云",
  altostratus: "高层云", nimbostratus: "雨层云", stratocumulus: "层积云", stratus: "层云",
  cumulus: "积云", cumulonimbus: "积雨云", unknown: "未分类",
};
const labels: Record<string, string> = {
  ...familyLabels, ...genusLabels,
  local_deck: "本地云幕", distant_target: "远方目标云", remote_boundary: "远方云边界",
  cloud_hole: "云洞透光", convective: "对流厚云", horizon_blocked: "日出方向云幕遮挡",
  solar_incoming_blocked_field: "太阳入射受阻云场", clear: "晴空 / 无有效目标", invalid: "无有效场景",
  golden: "金黄", orange_red: "橙红", deep_red: "深红", muted_glow: "闷烧",
  ice: "冰相成分", mixed: "冰水混合成分", water: "液态水成分",
  forming: "正在生成", strengthening: "正在增强", steady: "基本稳定", weakening: "正在减弱", dissipating: "正在消散",
  detected: "检出低云", unknown: "未知", observation: "原始观测", strong: "可靠", decayed: "已衰减",
  "diagnostic-only": "仅供诊断", retrieved: "已反演", available: "可用", partial_unknown: "部分未知", unavailable: "不可用",
  gfs: "GFS", ec: "EC", ecmwf: "EC", satellite: "卫星实况",
};
export const label = (value: unknown): string => typeof value === "string" ? labels[value] ?? value : "--";
export function displayTime(value: unknown, timezone: string, includeDate = false): string {
  if (typeof value !== "string" || !value) return "--";
  if (!/(?:z|[+-]\d{2}:\d{2})$/i.test(value)) {
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(value);
    return match ? `${includeDate ? `${match[1]} ` : ""}${match[2]}` : "--";
  }
  const stamp = Date.parse(value);
  if (!Number.isFinite(stamp)) return "--";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: timezone, ...(includeDate ? { year: "numeric", month: "2-digit", day: "2-digit" } as const : {}),
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(stamp);
  } catch { return "--"; }
}
export function summaryLines(report: RecordValue | null): string[] {
  const summary = report?.summary;
  return Array.isArray(summary) ? summary.filter((item): item is string => typeof item === "string" && !!item.trim())
    : typeof summary === "string" && summary.trim() ? [summary] : [];
}
export function range(low: unknown, high: unknown, unit: string, scale = 1, digits = 1): string {
  const a = numeric(low), b = numeric(high);
  return a === null || b === null ? "--" : `${(a / scale).toFixed(digits)} - ${(b / scale).toFixed(digits)} ${unit}`;
}
export function signed(value: unknown): string {
  const number = numeric(value);
  return number === null ? "--" : `${number > 0 ? "+" : ""}${number.toFixed(3)}`;
}
export interface DetailRow { label: string; value: string }
export interface DetailSection { title: string; rows: DetailRow[] }
type Style = "number" | "percent" | "factor" | "text" | "km" | "minutes" | "signed";
type Spec = [string, string, Style?];
const valueText = (value: unknown, style: Style = "number"): string => {
  if (style === "text") return typeof value === "boolean" ? value ? "是" : "否" : label(value);
  if (style === "percent") return percent(value);
  if (style === "signed") return signed(value);
  const n = numeric(value);
  if (n === null) return "--";
  return style === "factor" ? `×${n.toFixed(3)}` : style === "km" ? `${n.toFixed(0)} km`
    : style === "minutes" ? `${n.toFixed(0)} 分钟` : n.toFixed(3);
};
function rows(data: RecordValue, specs: Spec[]): DetailRow[] {
  return specs.map(([key, title, style]) => ({
    label: title,
    value: key === "satellite_low_cloud_state" && data[key] === "clear" ? "未检出低云"
      : key === "satellite_retrieval_quality"
        ? ({ high: "高", medium: "中", low: "低", unknown: "未知" } as Record<string, string>)[String(data[key])] ?? "--"
        : valueText(data[key], style),
  }));
}
export function qualitySections(report: RecordValue | null): DetailSection[] {
  if (!report) return [];
  const q = record(report.quality_components);
  return [
    { title: "前方与本地云场", rows: rows(q, [
      ["forward_view_cloud_field_amount_peak", "前方总云场量", "percent"],
      ["forward_burnable_cloud_field_amount_peak", "前方可烧云量", "percent"],
      ["forward_burnable_cloud_ratio_peak", "可烧 / 总云量", "percent"],
      ["forward_burnable_cloud_field_amount_effective", "有效可烧云量", "percent"],
      ["forward_burnable_cloud_load_effective", "有效可烧云载荷"],
      ["forward_burnable_cloud_load_peak", "峰值可烧云载荷"],
      ["forward_view_cloud_field_amount_mean", "总云场时段均值", "percent"],
      ["forward_burnable_cloud_field_amount_mean", "可烧云场时段均值", "percent"],
      ["observer_local_cloud_field_amount_peak", "本地总云柱", "percent"],
      ["observer_local_burnable_cloud_field_amount_peak", "本地可烧云柱", "percent"],
      ["observer_local_burnable_cloud_load_peak", "本地可烧载荷"],
      ["observer_column_quality_index", "本地云柱诊断分"],
      ["observer_column_quality_peak", "本地云柱峰值分"],
    ]) },
    { title: "评分与修正", rows: [
      ...rows(q, [
        ["coverage_base_score", "云量基础分"], ["coverage_score", "云量得分"],
        ["duration_minutes", "持续时间", "minutes"], ["duration_factor", "持续时间系数", "factor"],
        ["duration_adjustment", "持续时间修正", "signed"], ["incoming_aod_adjustment", "入射 AOD 修正", "signed"],
        ["observer_aod_adjustment", "观测 AOD 修正", "signed"], ["raw_quality_index", "修正后原始分"],
        ["quality_score_cap", "质量分上限"], ["quality_limiting_factor", "当前限制因素", "text"],
      ]),
      { label: "最终质量分 / 2.5", value: format(report.quality_index ?? report.peak_quality_index, 3) },
    ] },
    { title: "光路与气溶胶", rows: [
      ...rows(q, [
        ["quality_aod550", "有效 AOD 550"], ["incoming_aod_illumination_factor", "太阳入射因子", "factor"],
        ["observer_aod_contrast_factor", "观测回程因子", "factor"],
        ["observer_return_optical_depth_mean", "观测回程光学厚度 τ"],
        ["observer_return_transmission_mean", "观测回程透过率 T", "percent"],
        ["solar_incoming_block_risk_at_best_cloud", "最佳云入射遮挡", "percent"],
        ["solar_incoming_block_risk_mean", "平均入射遮挡", "percent"],
        ["forward_field_radius_km", "太阳方向分析范围", "km"],
        ["forward_sector_half_angle_deg", "太阳方向半角 (°)"], ["quality_coverage_source", "可烧量来源", "text"],
        ["cloud_optics_source", "云光学来源", "text"], ["effective_radius_source", "有效半径来源", "text"],
        ["cloud_optical_depth_source", "云光学厚度来源", "text"],
      ]),
      ...rows(record(report.aerosol), [["aod550", "本地 AOD 550"], ["source", "气溶胶来源", "text"], ["quality", "气溶胶质量", "text"]]),
    ] },
  ];
}
export function nowcastSections(report: RecordValue | null): DetailSection[] {
  const n = record(report?.nowcast);
  if (!Object.keys(n).length) return [];
  return [
    { title: "卫星云场与多光谱", rows: rows(n, [
      ["source", "卫星来源", "text"], ["image_channel", "图像通道", "text"],
      ["near_cloud_ratio", "近场云量", "percent"], ["far_cloud_ratio", "远场云量", "percent"],
      ["infrared_near_cloud_ratio", "红外近场云比", "percent"], ["infrared_far_cloud_ratio", "红外远场云比", "percent"],
      ["visible_near_cloud_ratio", "可见光近场云比", "percent"], ["visible_far_cloud_ratio", "可见光远场云比", "percent"],
      ["local_cloud_ratio", "本地云量", "percent"], ["horizon_cloud_ratio", "地平线云量", "percent"],
      ["thin_cloud_ratio", "薄云比例", "percent"], ["thick_cloud_ratio", "厚云比例", "percent"],
      ["cloud_blanket_score", "云幕评分", "percent"], ["cloud_texture_score", "云体纹理", "percent"],
      ["cloud_uniformity_score", "云体均匀度", "percent"], ["horizon_clearance_score", "地平线通透", "percent"],
      ["cloud_type_label", "多光谱云类", "text"], ["cloud_type_confidence", "云类置信度", "percent"],
      ["altocumulus_probability", "高积云概率", "percent"], ["altostratus_probability", "高层云概率", "percent"],
      ["cirrus_probability", "卷云概率", "percent"], ["ice_cloud_probability", "冰云概率", "percent"],
      ["water_cloud_probability", "水云概率", "percent"], ["thin_cirrus_probability", "薄卷云概率", "percent"],
    ]) },
    { title: "云体运动、雷达与约束", rows: rows(n, [
      ["satellite_motion_quality", "卫星运动质量", "text"], ["satellite_motion_age_minutes", "运动场年龄", "minutes"],
      ["motion_dx_px_per_hour", "东西运动 (px/h)"], ["motion_dy_px_per_hour", "南北运动 (px/h)"],
      ["motion_correlation", "运动相关性"], ["satellite_advection_horizon_hours", "平流有效时长 (h)"],
      ["radar_source", "雷达来源", "text"], ["radar_age_minutes", "雷达年龄", "minutes"],
      ["radar_coverage_ratio", "雷达覆盖", "percent"], ["radar_local_precip_ratio", "本地降水比例", "percent"],
      ["radar_path_precip_ratio", "路径降水比例", "percent"], ["radar_max_dbz", "最大回波 (dBZ)"],
      ["radar_convective_risk", "雷达对流风险", "percent"], ["radar_reliability_score", "雷达可靠度", "percent"],
      ["effective_convective_risk", "有效对流风险", "percent"], ["convective_score_ceiling", "对流评分上限", "percent"],
      ["effective_path_blocking_risk", "有效光路阻塞", "percent"], ["calibrated_sample_coverage_ratio", "有效采样覆盖", "percent"],
      ["geometric_path_samples", "几何光路采样数"],
      ["satellite_retrieval_quality", "卫星反演质量", "text"], ["satellite_low_cloud_state", "低云状态", "text"],
      ["vertical_prior_source", "垂直先验来源", "text"], ["vertical_prior_identity", "垂直先验标识", "text"],
    ]) },
  ];
}
export function reportPaths(result: RecordValue | null, browser = false): Array<{ label: string; path: string }> {
  if (!result) return [];
  const candidates: Array<[string, RecordValue]> = [["完整诊断报告", result]];
  for (const [id, value] of Object.entries(record(result.model_results))) candidates.push([`${id.toUpperCase()} 诊断报告`, record(value)]);
  const seen = new Set<string>();
  return candidates.flatMap(([name, report]) => {
    const path = browser ? report.html_report_url : report.html_report_path;
    if (typeof path !== "string" || !path || seen.has(path)) return [];
    if (browser && !/^\/api\/v1\/firesky\/reports\/[a-f0-9]{64}(?:\.html)?$/.test(path)) return [];
    seen.add(path);
    return [{ label: name, path }];
  });
}

export interface SpectralColorSummary {
  peakHex: string;
  peakLabelZh: string;
  peakLabelEn: string;
  gradientHex: string[];
  gradientCss: string;
  solarAzimuthDeg: number | null;
  solarElevationDeg: number | null;
}

const HEX_COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;

export function spectralColor(report: RecordValue | null): SpectralColorSummary | null {
  const raw = record(report?.spectral_color);
  if (!Object.keys(raw).length) return null;
  const peakHex = typeof raw.peak_hex === "string" && HEX_COLOR_PATTERN.test(raw.peak_hex.trim())
    ? raw.peak_hex.trim().toUpperCase()
    : "";
  const peakLabelZh = typeof raw.peak_label_zh === "string" ? raw.peak_label_zh.trim() : "";
  const peakLabelEn = typeof raw.peak_label_en === "string" ? raw.peak_label_en.trim() : "";
  const rawStops = Array.isArray(raw.sky_gradient_hex) ? raw.sky_gradient_hex : [];
  const gradientHex = rawStops
    .filter((item): item is string => typeof item === "string" && HEX_COLOR_PATTERN.test(item.trim()))
    .map((item) => item.trim().toUpperCase());
  if (!peakHex && !peakLabelZh && !peakLabelEn && gradientHex.length === 0) return null;
  const stops = gradientHex.length >= 2 ? gradientHex : (peakHex ? [peakHex, peakHex] : []);
  const gradientCss = stops.length >= 2 ? `linear-gradient(90deg, ${stops.join(", ")})` : "";
  return {
    peakHex,
    peakLabelZh,
    peakLabelEn,
    gradientHex,
    gradientCss,
    solarAzimuthDeg: numeric(raw.solar_azimuth_deg),
    solarElevationDeg: numeric(raw.solar_elevation_deg),
  };
}

export interface AzimuthRadarBearing {
  deg: number;
  labelZh: string;
  labelEn: string;
  score: number;
  canvas: number | null;
  lowBlock: number | null;
}

export interface AzimuthRadarSummary {
  solarAzimuthDeg: number | null;
  bestAzimuthDeg: number | null;
  bestDirectionZh: string;
  bestDirectionEn: string;
  bestScore: number | null;
  recommendationZh: string;
  recommendationEn: string;
  bearings: AzimuthRadarBearing[];
  polygonPoints: string;
  bestPoint: { x: number; y: number } | null;
  solarPoint: { x: number; y: number } | null;
}

export function polarPoint(
  deg: number,
  normalizedRadius: number,
  cx = 80,
  cy = 80,
  maxR = 54,
): { x: number; y: number } {
  const r = Math.max(0, Math.min(1, normalizedRadius)) * maxR;
  const rad = (deg * Math.PI) / 180;
  return {
    x: Number((cx + r * Math.sin(rad)).toFixed(2)),
    y: Number((cy - r * Math.cos(rad)).toFixed(2)),
  };
}

export function azimuthRadar(report: RecordValue | null): AzimuthRadarSummary | null {
  const raw = record(report?.azimuth_radar);
  if (!Object.keys(raw).length) return null;
  const solarAzimuthDeg = numeric(raw.solar_azimuth_deg);
  const bestAzimuthDeg = numeric(raw.best_azimuth_deg);
  const bestDirectionZh = typeof raw.best_direction_zh === "string" ? raw.best_direction_zh.trim() : "";
  const bestDirectionEn = typeof raw.best_direction_en === "string" ? raw.best_direction_en.trim() : "";
  const bestScore = numeric(raw.best_score);
  const recommendationZh = typeof raw.recommendation_zh === "string" ? raw.recommendation_zh.trim() : "";
  const recommendationEn = typeof raw.recommendation_en === "string" ? raw.recommendation_en.trim() : "";
  const rawBearings = Array.isArray(raw.bearings) ? raw.bearings : [];
  const bearings: AzimuthRadarBearing[] = [];
  for (const item of rawBearings) {
    const row = record(item);
    const deg = numeric(row.deg);
    const score = numeric(row.score);
    if (deg === null || score === null) continue;
    bearings.push({
      deg,
      labelZh: typeof row.label_zh === "string" ? row.label_zh : "",
      labelEn: typeof row.label_en === "string" ? row.label_en : "",
      score: Math.max(0, Math.min(1, score)),
      canvas: numeric(row.canvas),
      lowBlock: numeric(row.low_block),
    });
  }
  if (
    bestAzimuthDeg === null
    && !bestDirectionZh
    && !bestDirectionEn
    && !recommendationZh
    && !recommendationEn
    && bearings.length === 0
  ) {
    return null;
  }
  const polygonPoints = bearings.length >= 3
    ? bearings.map((b) => {
      const p = polarPoint(b.deg, b.score);
      return `${p.x},${p.y}`;
    }).join(" ")
    : "";
  return {
    solarAzimuthDeg,
    bestAzimuthDeg,
    bestDirectionZh,
    bestDirectionEn,
    bestScore,
    recommendationZh,
    recommendationEn,
    bearings,
    polygonPoints,
    bestPoint: bestAzimuthDeg !== null ? polarPoint(bestAzimuthDeg, 1) : null,
    solarPoint: solarAzimuthDeg !== null ? polarPoint(solarAzimuthDeg, 1) : null,
  };
}
