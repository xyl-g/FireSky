const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const ZONED_ISO_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;

function hasExactUtcCalendarParts(
  year: number,
  month: number,
  day: number,
  hour = 0,
  minute = 0,
  second = 0,
): boolean {
  const parsed = new Date(0);
  parsed.setUTCHours(0, 0, 0, 0);
  parsed.setUTCFullYear(year, month - 1, day);
  parsed.setUTCHours(hour, minute, second, 0);
  return (
    parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day
    && parsed.getUTCHours() === hour
    && parsed.getUTCMinutes() === minute
    && parsed.getUTCSeconds() === second
  );
}

export function requireStrictIsoDate(value: unknown, label: string): string {
  if (typeof value !== 'string') {
    throw new Error(`${label} must be an ISO calendar date`);
  }
  const match = ISO_DATE_PATTERN.exec(value);
  if (match === null) {
    throw new Error(`${label} must be an ISO calendar date`);
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (
    year < 1
    || !hasExactUtcCalendarParts(year, month, day)
    || !Number.isFinite(Date.parse(`${value}T00:00:00Z`))
  ) {
    throw new Error(`${label} must be an ISO calendar date`);
  }
  return value;
}

export function requireStrictZonedIsoTime(value: unknown, label: string): string {
  if (typeof value !== 'string') {
    throw new Error(`${label} must be a timezone-aware ISO timestamp`);
  }
  const match = ZONED_ISO_TIME_PATTERN.exec(value);
  if (match === null) {
    throw new Error(`${label} must be a timezone-aware ISO timestamp`);
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const timezone = match[7];
  if (timezone !== 'Z') {
    const offsetHour = Number(timezone.slice(1, 3));
    const offsetMinute = Number(timezone.slice(4, 6));
    if (offsetMinute > 59 || offsetHour > 14 || (offsetHour === 14 && offsetMinute !== 0)) {
      throw new Error(`${label} must be a timezone-aware ISO timestamp`);
    }
  }
  if (
    year < 1
    || hour > 23
    || minute > 59
    || second > 59
    || !hasExactUtcCalendarParts(year, month, day, hour, minute, second)
    || !Number.isFinite(Date.parse(value))
  ) {
    throw new Error(`${label} must be a timezone-aware ISO timestamp`);
  }
  return value;
}

export function requireStrictUtcIsoTime(value: unknown, label: string): string {
  const text = requireStrictZonedIsoTime(value, label);
  if (!text.endsWith('Z') && !text.endsWith('+00:00')) {
    throw new Error(`${label} must use UTC offset Z or +00:00`);
  }
  return text;
}
