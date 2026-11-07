import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';

const vite = await createServer({
  root: process.cwd(),
  server: { middlewareMode: true },
  configFile: `${process.cwd()}/vite.config.ts`,
});
const {
  forecastDateOptions,
  isChinaDateOnOrAfterToday,
  refreshForecastDateSelection,
} = await vite.ssrLoadModule('/src/lib/utils/format.ts') as typeof import('../src/lib/utils/format.ts');
after(async () => vite.close());

const afterChinaMidnight = new Date('2026-08-27T00:01:00+08:00');

test('date shortcuts use China calendar dates and include the displayed month-day', () => {
  const options = forecastDateOptions(afterChinaMidnight);

  assert.deepEqual(options, [
    { value: '2026-08-27', label: '今天' },
    { value: '2026-08-28', label: '明天' },
    { value: '2026-08-29', label: '后天' },
  ]);
});

test('preserves a valid custom future date outside the three shortcuts', () => {
  const refreshed = refreshForecastDateSelection(
    '2026-08-26',
    '2026-09-03',
    afterChinaMidnight,
  );

  assert.equal(refreshed.targetDate, '2026-09-03');
  assert.equal(isChinaDateOnOrAfterToday(refreshed.targetDate, afterChinaMidnight), true);
});

test('rolls the old shortcut today forward after China midnight', () => {
  const refreshed = refreshForecastDateSelection(
    '2026-08-26',
    '2026-08-26',
    afterChinaMidnight,
  );

  assert.equal(refreshed.targetDate, '2026-08-27');
  assert.equal(refreshed.options[0]?.value, '2026-08-27');
});

test('rejects invalid calendar dates and falls back to China today', () => {
  const refreshed = refreshForecastDateSelection(
    '2026-08-26',
    '2026-02-30',
    afterChinaMidnight,
  );

  assert.equal(refreshed.targetDate, '2026-08-27');
  assert.equal(isChinaDateOnOrAfterToday('2026-02-30', afterChinaMidnight), false);
});
