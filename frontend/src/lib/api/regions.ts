import type { RegionalForecastArea } from './types';

/**
 * Operational regions shared by the compute and outlook pages. These match the
 * backend REGION_BBOXES / REGIONAL_FORECAST_AREAS vocabulary so a region computed
 * here reuses the data-assets prefetch cache.
 *
 * bounds are [west, south, east, north] — the order consumed by the map component.
 */
export const REGION_OPTIONS: { value: RegionalForecastArea; label: string }[] = [
  { value: 'china', label: '中国' },
  { value: 'east-asia', label: '东亚' },
  { value: 'southeast-asia', label: '东南亚' },
  { value: 'south-asia', label: '南亚' },
  { value: 'europe', label: '欧洲' },
  { value: 'north-america', label: '北美' },
  { value: 'south-america', label: '南美' },
  { value: 'oceania', label: '大洋洲' },
  { value: 'africa-middle-east', label: '非洲及中东' },
  { value: 'north-asia', label: '北亚' },
];

export const REGION_OPTIONS_WITH_CUSTOM: { value: RegionalForecastArea; label: string }[] = [
  ...REGION_OPTIONS,
  { value: 'custom', label: '自定义区域' },
];

export const REGION_BOUNDS: Record<
  Exclude<RegionalForecastArea, 'custom'>,
  [number, number, number, number]
> = {
  china: [73.0, 18.0, 136.0, 54.0],
  'east-asia': [63.0, 12.0, 155.0, 60.0],
  'southeast-asia': [85.0, -20.0, 155.0, 35.0],
  'south-asia': [55.0, -5.0, 110.0, 45.0],
  'europe': [-25.0, 25.0, 50.0, 75.0],
  'north-america': [-155.0, 5.0, -40.0, 80.0],
  'south-america': [-90.0, -60.0, -25.0, 20.0],
  'oceania': [100.0, -60.0, 180.0, 5.0],
  'africa-middle-east': [-25.0, -40.0, 65.0, 40.0],
  'north-asia': [20.0, 35.0, 180.0, 82.0],
};

export function regionLabel(code: RegionalForecastArea): string {
  return REGION_OPTIONS_WITH_CUSTOM.find((o) => o.value === code)?.label ?? code;
}
