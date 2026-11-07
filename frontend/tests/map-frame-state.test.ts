import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const forecastSource = await readFile(
  new URL('../src/routes/(app)/forecast/+page.svelte', import.meta.url),
  'utf8',
);
const timelineSource = await readFile(
  new URL('../src/lib/components/map/Timeline.svelte', import.meta.url),
  'utf8',
);

test('a forecast without map data clears the previous map frame', () => {
  assert.match(forecastSource, /forecast = data;[\s\S]{0,80}currentFrame = null;/);
});

test('forecast map frame callbacks are bound to the active map token', () => {
  assert.match(forecastSource, /onFrameLoaded=\{handleFrameLoaded\}/);
  assert.match(timelineSource, /onFrameLoaded\(frame, index, mapToken\)/);
  assert.match(forecastSource, /function handleFrameLoaded\(frame: MapFramePayload, _index: number, mapToken: string\)/);
  assert.match(forecastSource, /if \(!forecast\?\.map_token \|\| forecast\.map_token !== mapToken\) return;/);
});

test('stale map frame requests cannot update the active timeline', () => {
  assert.match(timelineSource, /let frameRequestSequence = 0;/);
  assert.match(timelineSource, /const requestSequence = \+\+frameRequestSequence;/);
  assert.match(timelineSource, /const mapToken = forecast\.map_token;/);
  assert.match(timelineSource, /if \(!isCurrentRequest\(\)\) return;/);
  assert.match(timelineSource, /if \(!isCurrentRequest\(\)\) return;\s*frameCache\.set\(time, frame\);/);
  assert.match(timelineSource, /if \(!isCurrentRequest\(\)\) return;\s*timeReadout = frame\.valid/);
  assert.match(timelineSource, /if \(!isCurrentRequest\(\)\) return;\s*onFrameLoaded\(frame, index, mapToken\);/);
});
