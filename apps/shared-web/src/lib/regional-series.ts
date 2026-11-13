import { offsetUtcTime } from "./forecast-time";
import type { RecordValue } from "./forecast-display";

type Encoding = "u8" | "u16" | "raw-u8";
interface FieldSpec { encoding: Encoding; scale?: number }
const fields: Record<string, FieldSpec> = {
  valid: { encoding: "raw-u8" },
  instant_quality_index: { encoding: "u16", scale: 0.001 },
  burnable_cloud_coverage: { encoding: "u8" },
  forward_view_cloud_field_amount: { encoding: "u8" },
  forward_burnable_cloud_field_amount: { encoding: "u8" },
  forward_burnable_cloud_ratio: { encoding: "u8" },
  vividness_score: { encoding: "u8" },
  solar_incoming_block_risk: { encoding: "u8" },
  observer_return_transmission: { encoding: "u8" },
  local_aod550: { encoding: "u16", scale: 0.001 },
};
const decoded = new WeakMap<object, Map<string, Uint8Array>>();

export interface RegionalPointTimeStep {
  utc: string;
  step: RecordValue;
}

function object(value: unknown): RecordValue | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as RecordValue : null;
}

function fieldBytes(frame: RecordValue, name: string, pointCount: number): Uint8Array | null {
  const spec = fields[name];
  const encoded = frame[name];
  if (!spec || typeof encoded !== "string") return null;
  let cache = decoded.get(frame);
  if (!cache) { cache = new Map(); decoded.set(frame, cache); }
  const existing = cache.get(name);
  if (existing) return existing;
  try {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
    const width = spec.encoding === "u16" ? 2 : 1;
    if (bytes.length !== pointCount * width) return null;
    cache.set(name, bytes);
    return bytes;
  } catch {
    return null;
  }
}

function fieldValue(frame: RecordValue, name: string, pointIndex: number, pointCount: number): number | null {
  const spec = fields[name];
  const bytes = fieldBytes(frame, name, pointCount);
  if (!spec || !bytes) return null;
  const raw = spec.encoding === "u16"
    ? bytes[pointIndex * 2] | (bytes[pointIndex * 2 + 1] << 8)
    : bytes[pointIndex];
  return spec.encoding === "u8" ? raw / 255 : spec.encoding === "u16" ? raw * (spec.scale ?? 1) : raw;
}

function selectedFrames(pointValue: unknown, frameDataValue: unknown) {
  const point = object(pointValue), frameData = object(frameDataValue);
  const pointCount = frameData?.point_count;
  const pointIndex = point?.frame_index;
  const anchor = point?.anchor_local;
  const frames = frameData?.frames;
  if (
    !point || !frameData || frameData.version !== 103
    || frameData.encoding !== "base64-quantized-row-major-v1"
    || !Number.isSafeInteger(pointCount) || (pointCount as number) < 1 || (pointCount as number) > 500_000
    || !Number.isSafeInteger(pointIndex) || (pointIndex as number) < 0 || (pointIndex as number) >= (pointCount as number)
    || typeof anchor !== "string" || !Array.isArray(frames) || frames.length !== 20
  ) return null;

  const selected = [];
  let previousOffset = -Infinity;
  for (const rawFrame of frames) {
    const frame = object(rawFrame);
    const offset = frame?.offset_minutes;
    if (!frame || typeof offset !== "number" || !Number.isInteger(offset) || offset <= previousOffset) return null;
    previousOffset = offset;
    const valid = fieldValue(frame, "valid", pointIndex as number, pointCount as number);
    const utc = offsetUtcTime(anchor, offset);
    if (valid === null || !utc) return null;
    selected.push({ frame, valid: valid > 0, valid_time_utc: utc });
  }
  return { selected, pointIndex: pointIndex as number, pointCount: pointCount as number };
}

/** Build the event-specific UTC timeline without expanding unrelated point metrics. */
export function regionalPointTimeSteps(pointValue: unknown, frameDataValue: unknown): RegionalPointTimeStep[] {
  return selectedFrames(pointValue, frameDataValue)?.selected.map(({ valid, valid_time_utc }) => ({
    utc: valid_time_utc, step: { valid, valid_time_utc },
  })) ?? [];
}

/** Expand only the selected point from Rust's compact regional frame matrix. */
export function regionalPointSeries(pointValue: unknown, frameDataValue: unknown): RecordValue[] {
  const selection = selectedFrames(pointValue, frameDataValue);
  if (!selection) return [];
  const rows: RecordValue[] = [];
  for (const { frame, valid, valid_time_utc } of selection.selected) {
    const values = Object.fromEntries(Object.keys(fields)
      .filter((name) => name !== "valid")
      .map((name) => [name, fieldValue(frame, name, selection.pointIndex, selection.pointCount)]));
    if (Object.values(values).some((value) => value === null)) return [];
    rows.push({ ...values, valid, valid_time_utc });
  }
  return rows;
}
