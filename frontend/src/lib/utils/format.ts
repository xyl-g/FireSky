/**
 * Date/time formatting utilities
 * Ported from map.html helper functions
 */

import { requireStrictIsoDate } from '../api/date-contract';

const SCORE_SCALE = 100;
export const CHINA_TIME_ZONE = 'Asia/Shanghai';

function datePartsInTimeZone(value: Date, timeZone: string): {
  year: number;
  month: number;
  day: number;
} {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value);
  const part = (type: 'year' | 'month' | 'day'): number => {
    const value = parts.find((item) => item.type === type)?.value;
    return Number(value);
  };
  return { year: part('year'), month: part('month'), day: part('day') };
}

/** Return a calendar date in China Standard Time, independent of browser TZ. */
export function chinaDateValue(dayOffset = 0, now = new Date()): string {
  const parts = datePartsInTimeZone(now, CHINA_TIME_ZONE);
  const shifted = new Date(Date.UTC(parts.year, parts.month - 1, parts.day + dayOffset));
  return shifted.toISOString().slice(0, 10);
}

/** Return the current hour in China Standard Time for local-hour controls. */
export function chinaHour(now = new Date()): number {
  const hour = new Intl.DateTimeFormat('en-US', {
    timeZone: CHINA_TIME_ZONE,
    hour: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now).find((item) => item.type === 'hour')?.value;
  return Number(hour ?? 0);
}

export function score(value: number | null | undefined): number {
  return Math.round(Number(value || 0) * SCORE_SCALE);
}

export function formatTime(
  value: string,
  timeZone: string = 'Asia/Shanghai',
): string {
  return new Date(value).toLocaleTimeString('zh-CN', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function formatLocalDateTime(
  value: string | null | undefined,
  timeZone: string = 'Asia/Shanghai',
): string {
  if (!value) return '暂无';
  return new Date(value).toLocaleString('zh-CN', {
    timeZone,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

/** Format model/data-run timestamps without converting their UTC identity. */
export function formatUtcDateTime(value: string | null | undefined): string {
  if (!value) return '暂无';
  return `${new Date(value).toLocaleString('zh-CN', {
    timeZone: 'UTC',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })} UTC`;
}

export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = String(Math.floor(total / 60)).padStart(2, '0');
  const rest = String(total % 60).padStart(2, '0');
  return `${minutes}:${rest}`;
}

export function modelName(value: string): string {
  return (
    { gfs: 'GFS', ec: 'ECMWF', ecmwf: 'ECMWF', satellite: '卫星实况' }[value] ||
    String(value || '').toUpperCase()
  );
}

export function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"]/g, (char) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[char]!,
  );
}

export function forecastDateOptions(now = new Date()): { value: string; label: string }[] {
  const labels = ['今天', '明天', '后天'];
  return labels.map((label, offset) => {
    return { value: chinaDateValue(offset, now), label };
  });
}

/** Refresh date labels after China Standard Time crosses midnight. */
export function refreshForecastDateSelection(
  previousToday: string | undefined,
  selectedDate: string,
  now = new Date(),
): { options: { value: string; label: string }[]; targetDate: string } {
  const options = forecastDateOptions(now);
  const today = options[0]?.value ?? previousToday ?? selectedDate;
  let validSelectedDate = false;
  try {
    requireStrictIsoDate(selectedDate, 'target date');
    validSelectedDate = true;
  } catch {
    validSelectedDate = false;
  }
  return {
    options,
    targetDate:
      selectedDate === previousToday || !validSelectedDate
        ? today
        : selectedDate,
  };
}

/** Validate a user-selected date against the China-local current calendar day. */
export function isChinaDateOnOrAfterToday(value: unknown, now = new Date()): value is string {
  if (typeof value !== 'string') return false;
  try {
    requireStrictIsoDate(value, 'target date');
  } catch {
    return false;
  }
  return value >= chinaDateValue(0, now);
}

/** GFS 公开数据发布上限：约 16 天（384 小时）。 */
export function gfsForecastMaxDate(now = new Date()): string {
  return chinaDateValue(16, now);
}

/** ECMWF Open Data 发布上限：约 10 天（240 小时）。 */
export function ecmwfForecastMaxDate(now = new Date()): string {
  return chinaDateValue(10, now);
}
