import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const load = (name, dependencies = {}) => {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, {
    exports, Uint8Array, WeakMap, Map, Number, Array, Object, Math,
    atob, require: (id) => dependencies[id],
  });
  return exports;
};
const forecastTime = load("forecast-time");
const { regionalPointSeries } = load("regional-series", { "./forecast-time": forecastTime });

function encodedFrame(index) {
  const bytes = new Uint8Array(2 * 10);
  bytes.set([1, index + 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  for (let field = 0; field < 2; field++) {
    const value = Math.round((index + 1) * 1000);
    bytes[field * 2] = value & 255;
    bytes[field * 2 + 1] = value >> 8;
  }
  const raw = (values) => btoa(String.fromCharCode(...values));
  return {
    offset_minutes: -20 + index * 5,
    valid: raw([1, 1]),
    instant_quality_index: raw([0xe8, 0x03, 0xd0, 0x07]),
    burnable_cloud_coverage: raw([255, 128]),
    forward_view_cloud_field_amount: raw([64, 128]),
    forward_burnable_cloud_field_amount: raw([32, 128]),
    forward_burnable_cloud_ratio: raw([128, 255]),
    vividness_score: raw([255, 64]),
    solar_incoming_block_risk: raw([10, 20]),
    observer_return_transmission: raw([200, 100]),
    local_aod550: raw([0xe8, 0x03, 0xd0, 0x07]),
  };
}

test("regional point series decodes the selected grid index and derives precise UTC", () => {
  const point = { frame_index: 1, anchor_local: "2026-11-11T17:00:00.123456+08:00" };
  const frameData = { version: 103, encoding: "base64-quantized-row-major-v1", point_count: 2,
    frames: Array.from({ length: 20 }, (_, index) => encodedFrame(index)) };
  const rows = regionalPointSeries(point, frameData);
  assert.equal(rows.length, 20);
  assert.equal(rows[0].valid_time_utc, "2026-11-11T08:40:00.123456Z");
  assert.equal(rows[0].instant_quality_index, 2);
  assert.equal(rows[0].burnable_cloud_coverage, 128 / 255);
  assert.equal(rows[0].local_aod550, 2);
  assert.equal(rows[19].valid_time_utc, "2026-11-11T10:15:00.123456Z");
});

test("regional point series rejects invalid frame identity, truncated arrays and out-of-range indexes", () => {
  const frameData = { version: 103, encoding: "base64-quantized-row-major-v1", point_count: 2,
    frames: Array.from({ length: 20 }, (_, index) => encodedFrame(index)) };
  assert.equal(regionalPointSeries({ frame_index: 2, anchor_local: "2026-11-11T17:00:00+08:00" }, frameData).length, 0);
  const truncated = { ...frameData, frames: [...frameData.frames] };
  truncated.frames[3] = { ...truncated.frames[3], local_aod550: "AA==" };
  assert.equal(regionalPointSeries({ frame_index: 0, anchor_local: "2026-11-11T17:00:00+08:00" }, truncated).length, 0);
});

test("UTC minute shifts preserve sub-millisecond precision and reject invalid offsets", () => {
  assert.equal(forecastTime.offsetUtcTime("2026-11-11T17:00:00.123456+08:00", -20), "2026-11-11T08:40:00.123456Z");
  assert.equal(forecastTime.offsetUtcTime("2026-11-11T17:00:00Z", 2.5), null);
});
