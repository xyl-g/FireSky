type Row = Record<string, unknown>;

/** Canonical UTC identity without discarding fractional seconds from Rust timestamps. */
export function utcTime(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/i.exec(value);
  if (!match) return null;
  const wall = Date.parse(`${match[1]}Z`);
  if (!Number.isFinite(wall) || new Date(wall).toISOString().slice(0, 19) !== match[1]) return null;
  const time = Date.parse(`${match[1]}${match[3]}`);
  if (!Number.isFinite(time)) return null;
  const fraction = (match[2] ?? "").replace(/0+$/, "");
  return new Date(time).toISOString().replace(".000Z", `${fraction ? `.${fraction}` : ""}Z`);
}

/** Shift an offset-aware timestamp without losing sub-millisecond digits. */
export function offsetUtcTime(value: unknown, offsetMinutes: number): string | null {
  const utc = utcTime(value);
  if (!utc || !Number.isInteger(offsetMinutes) || Math.abs(offsetMinutes) > 1440) return null;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z$/.exec(utc);
  if (!match) return null;
  const epoch = Date.parse(`${match[1]}Z`);
  if (!Number.isFinite(epoch)) return null;
  const shifted = new Date(epoch + offsetMinutes * 60_000).toISOString().slice(0, 19);
  if (!/^\d{4}-/.test(shifted)) return null;
  return `${shifted}${match[2] ? `.${match[2]}` : ""}Z`;
}

export function stepUtc(step: Row): string | null {
  return utcTime(step.valid_time_utc) ?? utcTime(step.valid_time_local);
}

export function forecastTimes(report: Row | null): Array<{ utc: string; step: Row }> {
  const seen = new Set<string>();
  return (Array.isArray(report?.steps) ? report.steps : []).flatMap((value) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return [];
    const step = value as Row;
    const utc = stepUtc(step);
    if (!utc || seen.has(utc)) return [];
    seen.add(utc);
    return [{ utc, step }];
  });
}

export function defaultForecastTime(report: Row | null): string | null {
  const times = forecastTimes(report);
  const best = utcTime(report?.best_time_utc) ?? utcTime(report?.best_time_local);
  if (best && times.some((row) => row.utc === best)) return best;
  const peak = times.filter(({ step }) => step.valid !== false && typeof step.instant_quality_index === "number"
    && Number.isFinite(step.instant_quality_index)).reduce<typeof times[number] | null>((best, row) =>
      !best || (row.step.instant_quality_index as number) > (best.step.instant_quality_index as number) ? row : best, null);
  return peak?.utc ?? times[0]?.utc ?? null;
}
