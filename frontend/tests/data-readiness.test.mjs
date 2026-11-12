import assert from 'node:assert/strict';
import test from 'node:test';
import { runnerImport } from 'vite';

const { module: { verifiedFetchReadiness } } = await runnerImport(
  './src/lib/api/data.ts', { configFile: false },
);

function fixture() {
  return {
    contract: 'rust-data-fetch-v1', status: 'done', ready: true, prepared: true, errors: [],
    // Native results are nested; readiness does not count legacy downloaded[].
    results: [{ source: 'gfs', result: { files: [{ path: '/fixture/gfs.grib2' }] } }],
    readiness: {
      contract: 'rust-data-readiness-v1', ready: true, prepared: true,
      frozen_plan: true, network_enabled: false,
      datasets: [{
        source: 'gfs', ready: true, prepared: true, required_files: 1, verified_files: 1,
        missing_count: 0, missing: [], errors: [],
      }],
    },
  };
}

test('native explicit nonempty frozen readiness is accepted', () => {
  const value = fixture();
  assert.equal(verifiedFetchReadiness(value, ['gfs']), value.readiness);
});

test('empty inventory and legacy downloaded arrays do not prove readiness', () => {
  assert.equal(verifiedFetchReadiness({
    status: 'done', prepared: false, missing_hours: [], required_hours_count: 0,
    results: [{ source: 'gfs', downloaded: ['a'] }],
  }, ['gfs']), null);
});

for (const [name, mutate] of [
  ['missing contract', (v) => { delete v.readiness.contract; }],
  ['false ready', (v) => { v.readiness.ready = false; }],
  ['zero requirements', (v) => { v.readiness.datasets[0].required_files = 0; v.readiness.datasets[0].verified_files = 0; }],
  ['partial cache', (v) => { v.readiness.datasets[0].verified_files = 0; }],
  ['wrong source', (v) => { v.readiness.datasets[0].source = 'cams'; }],
  ['missing rows', (v) => { v.readiness.datasets = []; }],
  ['malformed row', (v) => { v.readiness.datasets = [null]; }],
  ['not frozen', (v) => { v.readiness.frozen_plan = false; }],
  ['source error', (v) => { v.readiness.datasets[0].errors = ['corrupt']; }],
  ['missing file', (v) => { v.readiness.datasets[0].missing = [{ hour: 3 }]; }],
  ['provider error', (v) => { v.errors = ['upstream failed']; }],
  ['cancelled', (v) => { v.status = 'cancelled'; }],
]) {
  test(`readiness rejects ${name}`, () => {
    const value = fixture();
    mutate(value);
    assert.equal(verifiedFetchReadiness(value, ['gfs']), null);
  });
}

test('all requested sources must be present', () => {
  assert.equal(verifiedFetchReadiness(fixture(), ['gfs', 'cams']), null);
  assert.equal(verifiedFetchReadiness(fixture(), []), null);
});
