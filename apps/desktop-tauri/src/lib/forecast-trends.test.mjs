import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const exports = {};
const timeExports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./forecast-time.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports: timeExports, Date, Set });
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./forecast-trends.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports, Date, Set, Intl, require: (id) => { assert.equal(id, "./forecast-time"); return timeExports; } });
const plain = (value) => JSON.parse(JSON.stringify(value));
const step = (minute, extra = {}) => ({
  valid: true, valid_time_local: `2026-11-11T18:${String(minute).padStart(2, "0")}:00+08:00`, ...extra,
});

test("auxiliary trends use actual Web fields, preserve numeric zero and reject coerced missing values", () => {
  const [sample] = exports.trendSamples({ steps: [step(0, {
    instant_quality_index: 0, quality_aod550: 0,
    forward_view_cloud_field_amount: 0,
    burnable_cloud_coverage: null,
    forward_burnable_cloud_field_amount: false,
    forward_burnable_cloud_ratio: "0",
    observer_return_transmission: 1,
    solar_incoming_block_risk: 0,
  })] });
  assert.equal(sample.quality, 0);
  assert.equal(sample.aod, 0);
  assert.deepEqual(plain(sample.values), {
    cloud: 0, coverage: null, burnable: null, ratio: null, transmission: 1, blocking: 0,
  });
});

test("each physical series breaks at its own missing or invalid sample", () => {
  const samples = exports.trendSamples({ steps: [
    step(0, { burnable_cloud_coverage: 0, observer_return_transmission: 1, quality_aod550: 0 }),
    step(5, { burnable_cloud_coverage: null, observer_return_transmission: .9, quality_aod550: null }),
    step(10, { burnable_cloud_coverage: .3, observer_return_transmission: .8, quality_aod550: .2 }),
    step(15, { valid: false, burnable_cloud_coverage: .8, observer_return_transmission: .7 }),
    step(20, { burnable_cloud_coverage: .4, observer_return_transmission: .6 }),
  ] });
  assert.deepEqual(plain(exports.trendSegments(samples, "coverage").map((line) => line.map((point) => point.value))), [[0], [.3], [.4]]);
  assert.deepEqual(plain(exports.trendSegments(samples, "transmission").map((line) => line.map((point) => point.value))), [[1, .9, .8], [.6]]);
  assert.deepEqual(plain(exports.trendSegments(samples, "aod").map((line) => line.map((point) => point.value))), [[0], [.2]]);
});

test("timestamp parsing accepts explicit offsets and never guesses the browser timezone", () => {
  const samples = exports.trendSamples({ steps: [
    step(0, { burnable_cloud_coverage: 0 }),
    { valid_time_local: "2026-11-11T18:05:00", burnable_cloud_coverage: .2 },
    { valid_time_utc: "2026-11-11T10:10:00Z", burnable_cloud_coverage: .4 },
    step(10, { burnable_cloud_coverage: .9 }),
  ] });
  assert.equal(samples.length, 3);
  assert.equal(samples[0].time, Date.parse("2026-11-11T10:00:00Z"));
  assert.equal(samples[1].time, null);
  assert.equal(exports.trendSegments(samples, "coverage").length, 2);
  assert.equal(exports.trendTime(samples[0].time, "Asia/Shanghai"), "18:00");
  assert.equal(exports.trendTime(samples[0].time, "America/New_York"), "05:00");
  assert.equal(exports.trendTime(samples[0].time, "bad/zone"), "--");
});

test("range guards and nonmonotonic times do not fabricate or connect physical measurements", () => {
  const samples = exports.trendSamples({ steps: [
    step(10, { burnable_cloud_coverage: 1 }),
    step(0, { burnable_cloud_coverage: .2 }),
    step(5, { burnable_cloud_coverage: 1.1, quality_aod550: -1 }),
    step(15, { burnable_cloud_coverage: Infinity, quality_aod550: false }),
  ] });
  assert.deepEqual(plain(exports.trendSegments(samples, "coverage").map((line) => line.map((point) => point.value))), [[1], [.2]]);
  assert.equal(samples[2].aod, null);
  assert.equal(samples[3].aod, null);
});

test("summary keeps zero peaks and AOD and excludes invalid steps without changing total count", () => {
  const samples = exports.trendSamples({ steps: [
    step(0, { instant_quality_index: 0, quality_aod550: 0 }),
    step(5, { valid: false, instant_quality_index: 2, quality_aod550: 1 }),
    step(10, { instant_quality_index: 0, quality_aod550: .2 }),
  ] });
  const summary = exports.trendSummary(samples);
  assert.equal(samples.length, 3);
  assert.equal(summary.validCount, 2);
  assert.equal(summary.peakIndex, 0);
  assert.equal(summary.aodMin, 0);
  assert.equal(summary.aodMax, .2);
  assert.equal(summary.lastTime - summary.firstTime, 600_000);
  assert.equal(exports.trendSummary([]).peakIndex, null);
  assert.equal(exports.trendSummary([]).aodMin, null);
});
