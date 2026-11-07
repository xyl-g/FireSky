import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const profileSource = await readFile(
  new URL('../src/lib/components/forecast/SatelliteVerticalProfile.svelte', import.meta.url),
  'utf8',
);
const nowSource = await readFile(
  new URL('../src/routes/(app)/now/+page.svelte', import.meta.url),
  'utf8',
);
const typesSource = await readFile(
  new URL('../src/lib/api/types.ts', import.meta.url),
  'utf8',
);

test('nowcast uses an auditable HSD/GFS satellite pressure profile', () => {
  assert.match(typesSource, /interface SatelliteVerticalProfileLevel/);
  assert.match(typesSource, /numeric_clear_sky_allowed: false/);
  assert.match(typesSource, /status: 'available' \| 'partial_unknown' \| 'unavailable'/);
  assert.match(nowSource, /<SatelliteVerticalProfile profile=\{forecast\.nowcast\.satellite_vertical_profile\}/);
  assert.match(profileSource, /HSD B07\/B13\/B15 云概率代理/);
  assert.match(profileSource, /model_cloud_fraction/);
  assert.match(profileSource, /class:unknown-cell/);
  assert.match(profileSource, /plot-scroll/);
  assert.match(profileSource, /未知，不解释为晴空/);
  assert.match(profileSource, /不发布 COT、云相或官方云高真值/);
  assert.doesNotMatch(nowSource, /<NowcastSolarPathProfile/);
});
