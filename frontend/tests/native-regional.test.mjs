import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { gunzipSync } from 'node:zlib';
import test from 'node:test';
import { runnerImport } from 'vite';

const { module: {
  normalizeRegionalForecastPayload, validateNowcastCloudContract, startNowcast, runNowcast,
} } = await runnerImport(
  './src/lib/api/forecast.ts', { configFile: false },
);
const { module: { validateMapFramePayload } } = await runnerImport(
  './src/lib/api/map-frame.ts', { configFile: false },
);

test('nowcast transports request map cells by default and preserve explicit false', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, options) => {
    requests.push({ path, body: JSON.parse(options.body) });
    return new Response(JSON.stringify({ job_id: 'fixture' }));
  };
  const body = {
    lat: 30, lon: 120, timezone_name: 'Asia/Shanghai', elevation_m: 50,
    target_date: '2026-11-11', phenomenon: 'dusk',
  };
  try {
    await startNowcast(body);
    await startNowcast({ ...body, include_map_cells: false });
    await assert.rejects(runNowcast(body), /missing the calibrated cloud-column contract/);
    await assert.rejects(
      runNowcast({ ...body, include_map_cells: false }),
      /missing the calibrated cloud-column contract/,
    );
    assert.deepEqual(requests.map(({ body: value }) => value.include_map_cells), [
      true, false, true, false,
    ]);
    assert.deepEqual(requests.map(({ path }) => path), [
      '/api/v1/firesky/forecasts/nowcast',
      '/api/v1/firesky/forecasts/nowcast',
      '/api/v1/firesky/forecasts/nowcast/compute',
      '/api/v1/firesky/forecasts/nowcast/compute',
    ]);
    assert.equal(Object.hasOwn(body, 'include_map_cells'), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

function fixture() {
  const precision = {
    mode: 'fixture-native', point_spacing_deg: 0.25, model_grid_spacing_deg: 0.25,
    point_count: 1, processed_sample_count: 20, candidate_limit_per_sample: 0,
    vertical_profiles: true, full_point_coverage: true, aerosol_profile_mode: 'cams-profile',
  };
  const quality = {
    burnable_cloud_coverage_effective: 0.5, forward_view_cloud_field_amount_peak: 0.6,
    forward_burnable_cloud_field_amount_peak: 0.3, forward_burnable_cloud_ratio_peak: 0.5,
    duration_minutes: 25, local_aod550: 0.1, quality_aod550: 0.1,
    incoming_aod_illumination_factor: 0.9, observer_aod_contrast_factor: 0.9,
    observer_return_optical_depth_mean: 0.1, observer_return_transmission_mean: 0.9,
    incoming_aod_adjustment: -0.01, observer_aod_adjustment: -0.01,
  };
  return {
    scope: 'regional', model_source: 'gfs', forecast_mode: 'model',
    point_mode: 'explicit-single-points', regions: ['custom'],
    coverage: [{
      code: 'custom', label: 'Fixture', bounds: [100, 0, 102, 1],
      timezone_name: 'Asia/Shanghai', point_prefix: 'fixture',
    }],
    target_date: '2026-11-09', phenomenon: 'dusk',
    cycle_time_utc: '2026-11-09T00:00:00Z',
    compute_backend_requested: 'auto', compute_backend: 'cpu',
    point_count: 1, success_count: 1, error_count: 0, errors: [], precision,
    native_forecast: {
      contract: 'rust-desktop-regional-forecast-v1', runtime_python_used: false,
      point_payload_complete: true, frame_payload_complete: true,
      public_forecast_complete: false,
    },
    cache: {
      contract: 'rust-native-runtime-cache-v1', status: 'refresh',
      key: 'a'.repeat(64), source_id: 'b'.repeat(64),
      generated_at_utc: '2026-11-11T00:00:00Z',
    },
    input_manifest: {
      contract: 'rust-runtime-input-provenance-v1',
      sources: ['model:f009', 'cams:2026-11-09T09:00:00Z'].map((role) => ({
        path: `/fixture/${role}`, role, identity: { bytes: 100, sha256: 'c'.repeat(64) },
      })),
    },
    timing: {
      prefetch_seconds: 0.1,
      native_timings_ms: {
        model_profiles_and_solar: 1, model_decode_and_source_hash: 2,
        model_pack_and_derive: 3, cams_assembly: 4, native_assessment: 5,
      },
    },
    frame_data: {
      version: 103, encoding: 'base64-quantized-row-major-v1', point_count: 1,
      precision: { ...precision },
      frames: Array.from({ length: 20 }, (_, i) => ({ offset_minutes: -20 + i * 5, valid: '/w==' })),
      performance: {
        total_seconds: 0.01, instant_scoring_seconds: 0, assembly_seconds: 0.01,
        points_per_second: 100, samples_per_second: 2000,
      },
    },
    points: [{
      name: 'Fixture', lat: 0.25, lon: 100.25, observer_elevation_asl_m: 50,
      quality_index: 0.7, quality_level: 'moderate', peak_quality_index: 0.8,
      peak_quality_level: 'moderate', dominant_burnable_pressure_family: 'middle',
      confidence_score: 0.8, best_time_local: '2026-11-09T18:00:00+08:00',
      quality_components: quality, high_cloud: 0.1, mid_cloud: 0.3, low_cloud: 0.1,
    }],
  };
}

test('native results keep an independent contract and real point values', () => {
  const normalized = normalizeRegionalForecastPayload(fixture());
  assert.equal(normalized.point_format, 'native-object-v1');
  assert.equal(normalized.algorithm, 'rust-desktop-regional-forecast-v1');
  assert.equal(normalized.points[0].burnable_duration_minutes, 25);
  assert.equal(normalized.points[0].quality_index, 0.7);
  assert.equal(normalized.cache.version, undefined);
  assert.deepEqual(normalized.data_provenance.forecast_hours, [9]);
});

for (const [name, mutate] of [
  ['Python runtime', (value) => { value.native_forecast.runtime_python_used = true; }],
  ['legacy completeness', (value) => { value.native_forecast.public_forecast_complete = true; }],
  ['source hash', (value) => { value.input_manifest.sources[0].identity.sha256 = 'bad'; }],
  ['missing CAMS', (value) => { value.input_manifest.sources.pop(); }],
  ['frame count', (value) => { value.frame_data.frames.pop(); }],
  ['frame precision', (value) => { value.frame_data.precision.point_count = 2; }],
  ['wrong frame time', (value) => { value.frame_data.frames[0].offset_minutes = 0; }],
  ['out of range quality', (value) => { value.points[0].quality_index = 3; }],
  ['missing quality', (value) => { delete value.points[0].quality_components.local_aod550; }],
  ['invalid coverage', (value) => { value.coverage[0].bounds = [102, 0, 100, 1]; }],
]) {
  test(`native adapter rejects ${name}`, () => {
    const value = fixture();
    mutate(value);
    assert.throws(() => normalizeRegionalForecastPayload(value));
  });
}

if (process.env.FIRESKY_NATIVE_REGIONAL_FIXTURE) {
  test('actual native GRIB regional output normalizes without legacy claims', async () => {
    const raw = JSON.parse(await readFile(process.env.FIRESKY_NATIVE_REGIONAL_FIXTURE, 'utf8'));
    for (const result of Object.values(raw.model_results)) {
      const normalized = normalizeRegionalForecastPayload(result);
      assert.equal(normalized.points.length, result.point_count);
      assert.equal(normalized.native_forecast.runtime_python_used, false);
    }
  });
}

if (process.env.FIRESKY_NATIVE_NOWCAST_FIXTURE) {
  const raw = JSON.parse(await readFile(process.env.FIRESKY_NATIVE_NOWCAST_FIXTURE, 'utf8'));
  const nowcast = raw.nowcast ?? raw.model_results?.gfs?.nowcast;
  test('actual native nowcast passes the unchanged cloud-column contract', () => {
    assert.ok(nowcast);
    validateNowcastCloudContract(nowcast);
  });
  test('native nowcast contract rejects missing algorithm identity', () => {
    const incomplete = structuredClone(nowcast);
    delete incomplete.nowcast_algorithm;
    assert.throws(() => validateNowcastCloudContract(incomplete));
  });
  test('native nowcast contract rejects missing cloud columns', () => {
    const incomplete = structuredClone(nowcast);
    delete incomplete.cloud_column_sets;
    assert.throws(() => validateNowcastCloudContract(incomplete));
  });
  if (process.env.FIRESKY_NATIVE_MAP_CACHE) {
    test('actual native maps retain payload, timeline, content and report identities', async () => {
      const report = raw.model_results?.gfs ?? raw;
      assert.equal(report.map_capability.available, true);
      const bytes = gunzipSync(await readFile(process.env.FIRESKY_NATIVE_MAP_CACHE));
      assert.equal(createHash('sha256').update(bytes).digest('hex').slice(0, 24), report.map_token);
      const cache = JSON.parse(bytes.toString('utf8'));
      assert.equal(cache.version, 25);
      assert.ok(cache.frames.length > 0);
      assert.deepEqual(
        cache.frames.map((frame) => validateMapFramePayload(frame).valid_time_local),
        report.map_times,
      );
      assert.ok(cache.frames.some((frame) => frame.cells.length > 0));
      const html = await readFile(report.html_report_path);
      assert.equal(
        report.html_report_url,
        `/api/v1/firesky/reports/${createHash('sha256').update(html).digest('hex')}.html`,
      );
    });
  }
}
