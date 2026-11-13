import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./forecast-display.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports, Date, Set, Map });
const plain = (value) => JSON.parse(JSON.stringify(value));

test("presentation preserves zero, absence and partial model failure", () => {
  assert.equal(exports.format(0), "0.00");
  assert.equal(exports.format(null), "--");
  assert.equal(exports.format(false), "--");
  assert.equal(exports.metricValue("burnable", { quality_components: { burnable_cloud_coverage_effective: 0 } }), "0%");
  assert.equal(exports.modelReport({ model_results: { gfs: { peak_quality_index: 1 } }, model_errors: { gfs: "failed" } }, "gfs"), null);
  assert.equal(exports.metricValue("aod", { aerosol: { aod550: 0 }, quality_aod550: 1 }), "0.00");
});
test("nowcast low-cloud labels use the engine's actual detected/clear/unknown enum", () => {
  for (const [value, expected] of [["detected", "检出低云"], ["clear", "未检出低云"], ["unknown", "未知"]]) {
    assert.equal(exports.metricValue("satellite_low_cloud_state", { nowcast: { satellite_low_cloud_state: value } }, true), expected);
  }
});
test("nowcast ceilings use normalized percentages and satellite confidence stays distinct from forecast confidence", () => {
  assert.equal(exports.metricValue("convective_score_ceiling", { nowcast: { convective_score_ceiling: .25 } }, true), "25%");
  assert.equal(exports.metricValue("confidence", { confidence_score: .8, nowcast: { confidence: 0 } }, true), "0%");
  assert.equal(exports.metricValue("confidence", { confidence_score: .8, nowcast: {} }, true), "--");
  assert.equal(exports.metricValue("confidence", { confidence_score: 0 }), "0%");
});
test("summary windows fall back to geometry and then supplied steps without invented times", () => {
  assert.equal(exports.windowLabel({ geometry_window: { start_time_local: "2026-11-11T18:00:00", end_time_local: "2026-11-11T18:20:00" } }), "18:00 - 18:20");
  assert.equal(exports.windowLabel({ steps: [{ valid_time_local: "2026-11-11T18:05:00" }, { valid_time_local: "2026-11-11T18:10:00" }] }), "18:05 - 18:10");
  assert.equal(exports.windowLabel(null), "-- - --");
});
test("curves keep missing samples as gaps and do not parse naive local timestamps", () => {
  const segments = exports.curveSegments({ steps: [
    { valid_time_utc: "2026-11-11T00:00:00Z", instant_quality_index: 0 },
    { valid_time_utc: "2026-11-11T00:05:00Z", instant_quality_index: null },
    { valid_time_utc: "2026-11-11T00:10:00Z", instant_quality_index: 1 },
    { valid_time_utc: "2026-11-11T00:10:00Z", instant_quality_index: 2 },
    { valid_time_local: "2026-11-11T08:15:00", instant_quality_index: 2 },
  ] });
  assert.deepEqual(plain(segments.map((segment) => segment.map((item) => item.value))), [[0], [1]]);
});
test("actual report offset-aware local timestamps render without a fabricated UTC field", () => {
  const [segment] = exports.curveSegments({ steps: [
    { valid_time_local: "2026-11-11T18:00:00+08:00", instant_quality_index: 0 },
    { valid_time_local: "2026-11-11T18:05:00+08:00", instant_quality_index: .5 },
  ] });
  assert.equal(segment.length, 2);
  assert.equal(segment[0].time, Date.parse("2026-11-11T10:00:00Z"));
});
test("regional grid includes errors without limiting cells or turning zero into missing", () => {
  assert.deepEqual(plain(exports.regionalCells({
    points: [{ lat: 0, lon: 0, peak_quality_index: 0 }, { lat: 1, lon: 1, quality_index: 2 }],
    errors: [{ lat: 1, lon: 1 }, { lat: 2, lon: 2 }, { lat: 91, lon: 0 }],
  })), [[0, 0, 0], [1, 1, null], [2, 2, null]]);
});
