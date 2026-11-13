import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./forecast-time.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports, Date, Set });
const { utcTime, stepUtc, forecastTimes, defaultForecastTime } = exports;

test("UTC identity retains microseconds and normalizes offset-equivalent instants", () => {
  assert.equal(utcTime("2026-11-11T18:00:00.123456+08:00"), "2026-11-11T10:00:00.123456Z");
  assert.equal(utcTime("2026-11-11T18:00:00.123456000+08:00"), "2026-11-11T10:00:00.123456Z");
  assert.equal(utcTime("2026-11-11T00:30:00.000001+01:00"), "2026-11-10T23:30:00.000001Z");
  assert.equal(stepUtc({ valid_time_utc: "invalid", valid_time_local: "2026-11-11T18:00:00+08:00" }), "2026-11-11T10:00:00Z");
});
test("naive, invalid and non-calendar timestamps cannot become query identities", () => {
  for (const time of [null, "2026-11-11T18:00:00", "2026-02-30T18:00:00Z", "2026-11-11T24:00:00Z", "2026-11-11", "2026-11-11T18:00:00+99:00"]) assert.equal(utcTime(time), null);
});
test("time choices deduplicate instants without rounding sub-millisecond rows", () => {
  const report = { steps: [
    { valid_time_utc: "2026-11-11T10:00:00.123456Z", instant_quality_index: 0 },
    { valid_time_local: "2026-11-11T18:00:00.123456+08:00" },
    { valid_time_utc: "2026-11-11T10:00:00.123457Z", instant_quality_index: 1 },
    { valid_time_local: "2026-11-11T18:00:00" },
  ] };
  assert.equal(forecastTimes(report).length, 2);
  assert.equal(defaultForecastTime(report), "2026-11-11T10:00:00.123457Z");
  assert.equal(defaultForecastTime({ ...report, best_time_local: "2026-11-11T18:00:00.123456+08:00" }), "2026-11-11T10:00:00.123456Z");
  assert.equal(defaultForecastTime({ steps: [] }), null);
});
