import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

async function renderTrendChart(props: Record<string, unknown>) {
  const vite = await createServer({
    root: process.cwd(),
    server: { middlewareMode: true },
    configFile: `${process.cwd()}/vite.config.ts`,
  });

  try {
    const module = await vite.ssrLoadModule('/src/lib/components/forecast/TrendChart.svelte');
    const { render } = await vite.ssrLoadModule('svelte/server');
    return render(module.default, { props }).body;
  } finally {
    await vite.close();
  }
}

const validStep = {
  valid_time_local: '2026-08-26T18:00:00+08:00',
  valid: true,
  instant_quality_index: 1.4,
  physically_burnable: true,
  burnable_cloud_coverage: 0.6,
  forward_burnable_cloud_ratio: 0.5,
  observer_return_transmission: 0.8,
  quality_aod550: 0.23,
};

test('TrendChart renders a meaningful empty state without plot scaffolding', async () => {
  const html = await renderTrendChart({
    steps: [],
    targetDate: '2026-08-26',
    cycleTimeUtc: '2026-08-26T06:00:00Z',
  });

  assert.match(html, /暂无逐时质量结果/);
  assert.match(html, /预测完成后，单点逐时质量、AOD550 和起报来源会显示在这里/);
  assert.match(html, /精确单点/);
  assert.doesNotMatch(html, /区域格点缓存/);
});

test('TrendChart exposes quality, AOD, source, and timeline contracts', async () => {
  const html = await renderTrendChart({
    steps: [validStep],
    targetDate: '2026-08-26',
    cycleTimeUtc: '2026-08-26T06:00:00Z',
  });

  assert.match(html, /质量与大气条件趋势/);
  assert.match(html, /AOD550/);
  assert.match(html, /事件时间轴/);
  assert.match(html, /精确单点/);
  assert.doesNotMatch(html, /区域格点缓存/);
  assert.doesNotMatch(html, /暂无逐时质量结果/);
});

test('TrendChart labels independent forecast model sources instead of assuming GFS', async () => {
  const ecHtml = await renderTrendChart({
    steps: [validStep],
    targetDate: '2026-08-26',
    cycleTimeUtc: '2026-08-26T06:00:00Z',
    modelSource: 'ec',
  });
  const satelliteHtml = await renderTrendChart({
    steps: [validStep],
    targetDate: '2026-08-26',
    modelSource: 'satellite',
  });

  assert.match(ecHtml, /ECMWF IFS/);
  assert.doesNotMatch(ecHtml, /GFS 起报/);
  assert.match(satelliteHtml, /实况/);
});

test('TrendChart omits invalid solar-window times', async () => {
  const html = await renderTrendChart({
    steps: [
      { ...validStep, valid_time_local: '2026-08-26T17:55:00+08:00', valid: false },
      validStep,
    ],
    targetDate: '2026-08-26',
    cycleTimeUtc: '2026-08-26T06:00:00Z',
  });

  assert.doesNotMatch(html, /17:55/);
  assert.match(html, /共 2 个时次，已隐藏无效时刻/);
  assert.doesNotMatch(html, /1\s*\/\s*2/);
});

test('TrendChart distinguishes an all-invalid window from missing results', async () => {
  const html = await renderTrendChart({
    steps: [{ ...validStep, valid: false }],
    targetDate: '2026-08-26',
    cycleTimeUtc: '2026-08-26T06:00:00Z',
  });

  assert.match(html, /当前事件窗口暂无有效时刻/);
  assert.match(html, /太阳几何或模式输入未在该窗口形成可评分时次/);
  assert.doesNotMatch(html, /暂无逐时质量结果/);
});
