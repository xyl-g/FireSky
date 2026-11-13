export type TrendKey = "cloud" | "coverage" | "burnable" | "ratio" | "transmission" | "blocking";
export type TrendValueKey = TrendKey | "aod";
export interface TrendDefinition {
  key: TrendKey;
  field: string;
  label: string;
  color: string;
  dash: string;
}
export const TREND_SERIES: TrendDefinition[] = [
  { key: "cloud", field: "forward_view_cloud_field_amount", label: "前方总云量", color: "var(--trend-cloud)", dash: "" },
  { key: "coverage", field: "burnable_cloud_coverage", label: "可烧云占比", color: "var(--fs-teal)", dash: "7 3" },
  { key: "burnable", field: "forward_burnable_cloud_field_amount", label: "前方可烧云量", color: "var(--trend-burnable)", dash: "2 3" },
  { key: "ratio", field: "forward_burnable_cloud_ratio", label: "可烧比例", color: "var(--trend-ratio)", dash: "8 3 2 3" },
  { key: "transmission", field: "observer_return_transmission", label: "回程透过率", color: "var(--trend-transmission)", dash: "4 3" },
  { key: "blocking", field: "solar_incoming_block_risk", label: "太阳入射阻挡", color: "var(--fs-error)", dash: "2 2" },
];
export interface TrendSample {
  utc: string | null;
  time: number | null;
  valid: boolean;
  quality: number | null;
  aod: number | null;
  values: Record<TrendKey, number | null>;
}
export interface TrendPoint { time: number; value: number }
export const finiteTrend = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

function ratio(value: unknown): number | null {
  const number = finiteTrend(value);
  return number !== null && number >= 0 && number <= 1 ? number : null;
}
/** Keep invalid rows in place so each missing measurement can break its own line. */
export function trendSamples(report: Record<string, unknown> | null, aodField = "quality_aod550"): TrendSample[] {
  if (!Array.isArray(report?.steps)) return [];
  const seen = new Set<string>();
  const samples: TrendSample[] = [];
  for (const raw of report.steps) {
    const step = raw !== null && typeof raw === "object" && !Array.isArray(raw)
      ? raw as Record<string, unknown> : {};
    const utc = stepUtc(step);
    const time = utc === null ? null : Date.parse(utc);
    if (utc !== null && seen.has(utc)) continue;
    if (utc !== null) seen.add(utc);
    const valid = step.valid !== false && time !== null;
    const values = Object.fromEntries(TREND_SERIES.map(({ key, field }) =>
      [key, valid ? ratio(step[field]) : null])) as Record<TrendKey, number | null>;
    const quality = finiteTrend(step.instant_quality_index);
    const aod = finiteTrend(step[aodField]);
    samples.push({
      utc, time, valid, values,
      quality: valid && quality !== null && quality >= 0 && quality <= 2.5 ? quality : null,
      aod: valid && aod !== null && aod >= 0 ? aod : null,
    });
  }
  return samples;
}

export function trendValue(sample: TrendSample, key: TrendValueKey): number | null {
  return key === "aod" ? sample.aod : sample.values[key];
}

export function trendSegments(samples: TrendSample[], key: TrendValueKey): TrendPoint[][] {
  const segments: TrendPoint[][] = [];
  let current: TrendPoint[] = [];
  const flush = () => { if (current.length) segments.push(current); current = []; };
  for (const sample of samples) {
    const value = trendValue(sample, key);
    if (!sample.valid || sample.time === null || value === null) {
      flush();
      continue;
    }
    if (current.length && sample.time <= current[current.length - 1].time) flush();
    current.push({ time: sample.time, value });
  }
  flush();
  return segments;
}

export function trendSummary(samples: TrendSample[]) {
  let peakIndex: number | null = null;
  let validCount = 0;
  const aod: number[] = [];
  const times: number[] = [];
  for (const [index, sample] of samples.entries()) {
    if (sample.time !== null) times.push(sample.time);
    if (!sample.valid) continue;
    validCount += 1;
    if (sample.aod !== null) aod.push(sample.aod);
    if (sample.quality !== null
      && (peakIndex === null || sample.quality > (samples[peakIndex].quality ?? -Infinity))) {
      peakIndex = index;
    }
  }
  return {
    validCount, peakIndex,
    firstTime: times.length ? Math.min(...times) : null,
    lastTime: times.length ? Math.max(...times) : null,
    aodMin: aod.length ? Math.min(...aod) : null,
    aodMax: aod.length ? Math.max(...aod) : null,
  };
}

export function trendTime(time: number | null, timezone: string): string {
  if (time === null || !Number.isFinite(time)) return "--";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: timezone, hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(time);
  } catch { return "--"; }
}
import { stepUtc } from "./forecast-time";

