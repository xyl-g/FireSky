import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const forecastSource = await readFile(
  new URL('../src/routes/(app)/forecast/+page.svelte', import.meta.url),
  'utf8',
);
const typesSource = await readFile(
  new URL('../src/lib/api/types.ts', import.meta.url),
  'utf8',
);
const scoreCardSource = await readFile(
  new URL('../src/lib/components/forecast/ScoreCard.svelte', import.meta.url),
  'utf8',
);
const breakdownSource = await readFile(
  new URL('../src/lib/components/forecast/QualityBreakdown.svelte', import.meta.url),
  'utf8',
);
const crossSectionSource = await readFile(
  new URL('../src/lib/components/forecast/NationalCrossSection.svelte', import.meta.url),
  'utf8',
);
const formatSource = await readFile(
  new URL('../src/lib/utils/format.ts', import.meta.url),
  'utf8',
);
test('point forecast exposes only the supported GFS and ECMWF model sources', () => {
  assert.match(typesSource, /ModelSource = 'gfs' \| 'ec' \| 'satellite'/);
  assert.match(forecastSource, /let modelSource: ModelSource = 'gfs'/);
  assert.match(forecastSource, /\{ value: 'gfs', label: 'GFS' \}/);
  assert.match(forecastSource, /\{ value: 'ec', label: 'ECMWF' \}/);
  assert.doesNotMatch(forecastSource, /ICON|icon_cycle|value: 'icon'/);
  assert.match(forecastSource, /queryModel === 'gfs' \|\| queryModel === 'ec'/);
});

test('point request keeps model-specific inputs separate', () => {
  assert.match(forecastSource, /modelSource === 'gfs' \? \{ gfs_resolution: '0p25'/);
  assert.match(forecastSource, /modelSource === 'ec'\s*\n\s*\? \{ _ec_cycle_override/);
  assert.doesNotMatch(forecastSource, /icon_cycle|_icon_cycle_override|modelSource === 'icon'/);
  assert.match(forecastSource, /satellite_source: 'off'/);
});

test('point result labels expose the EC cloud-boundary contract', () => {
  assert.doesNotMatch(scoreCardSource, /ICON|icon:/);
  assert.match(breakdownSource, /EC TCC \+ PL-RH 云柱代理/);
  assert.match(breakdownSource, /EC 公共 Open Data/);
  assert.doesNotMatch(breakdownSource, /ICON|model-level|HHL/);
  assert.doesNotMatch(breakdownSource, /基于观测点前方 GFS 全量云场/);
});

test('EC point results keep the shared profile and hourly sections', () => {
  assert.match(forecastSource, /<TrendChart steps=\{forecast\.steps\}/);
  assert.match(forecastSource, /<HourlyTable steps=\{forecast\.steps\}/);
  assert.match(forecastSource, /<NationalCrossSection section=\{crossSection\}/);
  assert.match(crossSectionSource, /section\?\.model_source === 'ec'/);
  assert.match(crossSectionSource, /ECMWF IFS/);
  assert.doesNotMatch(crossSectionSource, /model_source === 'ec'[\s\S]{0,300}GFS 地形海拔/);
});

test('removed ICON forecast compatibility is absent', () => {
  assert.doesNotMatch(formatSource, /iconForecastMaxDate|ICON/);
  assert.doesNotMatch(forecastSource, /iconForecastMaxDate|ICON|_icon_cycle_override/);
});

test('deep links accept only current model sources', () => {
  const modelAssignment = forecastSource.indexOf("if (queryModel === 'gfs' || queryModel === 'ec') modelSource = queryModel;");
  const dateValidation = forecastSource.indexOf('let queryDateAccepted = false;');
  assert.ok(modelAssignment >= 0);
  assert.ok(dateValidation >= 0);
  assert.ok(modelAssignment < dateValidation);
  assert.doesNotMatch(forecastSource, /queryModel === 'icon'|深链日期超出当前可发布 cycle/);
});
