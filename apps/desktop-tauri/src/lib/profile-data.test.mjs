import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./profile-data.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports, Set, Map });
const plain = (value) => JSON.parse(JSON.stringify(value));
const level = (height, probability, extra = {}) => ({
  height_m: height, pressure_hpa: 500, hsd_posterior_percent: probability, status: "retrieved", ...extra,
});
const sample = (distance, bearing, levels, extra = {}) => ({
  distance_km: distance, bearing_deg: bearing, status: "retrieved", levels, ...extra,
});
const satellite = (samples, extra = {}, bearing = 270) => ({
  cross_section: { solar_azimuth_deg: bearing },
  nowcast: { satellite_vertical_profile: { status: "available", samples, ...extra } },
});

test("satellite sections select only the solar bearing across the 360-degree boundary", () => {
  const data = exports.profileData(satellite([
    sample(30, 20, [level(3000, 80)]), sample(10, 359.9, [level(3000, 0)]),
    sample(30, 359.9, [level(3000, 25)]), sample(10, 359.7, [level(3000, 90)]),
  ], {}, .1), true);
  assert.deepEqual(plain(data.cells.map((cell) => [cell.distance, cell.probability])), [[10, 0], [30, 25]]);
  assert.equal(data.maxDistance, 35);
});

test("a fan without solar bearing is not overlaid into a false single section", () => {
  const data = exports.profileData(satellite([
    sample(10, 250, [level(3000, 90)]), sample(10, 270, [level(3000, 40)]),
  ], {}, null), true);
  assert.equal(data.cells.length, 0);
  assert.equal(data.empty, "暂无可用卫星剖面采样");
});

test("satellite unknown columns and levels remain missing, while valid zero remains numeric", () => {
  const data = exports.profileData(satellite([
    sample(0, 270, [level(1000, 0), level(3000, 80, { status: "unknown" })]),
    sample(20, 270, [level(1000, 90)], { status: "unknown" }),
    sample(40, 270, [level(null, 80), level(NaN, 80), level(-1, 80)]),
  ]), true);
  assert.deepEqual(plain(data.cells.map((cell) => cell.probability)), [0, null, null]);
  assert.match(exports.profileCellLabel(data.cells[0], true), /后验概率 0%/);
  assert.match(exports.profileCellLabel(data.cells[1], true), /后验概率 缺测/);
  assert.ok(data.cells.every((cell) => [cell.start, cell.end, cell.bottom, cell.top].every(Number.isFinite)));
});

test("unavailable satellite data does not render retained samples as observations", () => {
  assert.equal(exports.profileData(satellite([
    sample(10, 270, [level(3000, 70)]),
  ], { status: "unavailable" }), true).cells.length, 0);
  assert.equal(exports.profileData(null, true).empty, "等待实况分析");
  assert.equal(exports.profileData(null, false).empty, "等待预测");
});

test("model section reads real pressure-level matrices without converting null to zero", () => {
  const data = exports.profileData({ cross_section: {
    model_source: "ec", distances_km: [0, 20], pressure_levels_hpa: [850, 500],
    fields: {
      geopotential_height_asl_m: [[1000, 5000], [1100, 5200]],
      cloud_fraction_percent: [[0, null], [40, null]],
      cloud_amount: [[.7, null], [.7, .2]],
      temperature_c: [[0, -20], [null, -22]], rh_percent: [[0, 80], [null, 70]],
    },
  } });
  assert.deepEqual(plain(data.cells.map((cell) => cell.probability)), [0, null, 40, 20]);
  assert.equal(data.cells[0].temperature, 0);
  assert.equal(data.cells[0].humidity, 0);
  assert.equal(data.cells[2].temperature, null);
  assert.match(data.source, /ECMWF/);
});

test("invalid probabilities remain unknown and zero pressure-level heights remain valid", () => {
  const data = exports.profileData(satellite([
    sample(10, 270, [level(0, -1), level(1000, 101), level(3000, "20")]),
  ]), true);
  assert.equal(data.cells.length, 3);
  assert.deepEqual(plain(data.cells.map((cell) => cell.probability)), [null, null, null]);
  assert.equal(data.cells[0].bottom, 0);
});

test("terrain and theoretical illumination keep gaps instead of inventing connecting geometry", () => {
  const result = exports.profileLineSegments([0, 10, 20, 30, 40], [0, null, 100, false, 200]);
  assert.deepEqual(plain(result), [[{ distance: 0, height: 0 }], [{ distance: 20, height: 100 }], [{ distance: 40, height: 200 }]]);
});

test("all supplied vertical cells are retained without a display top-N limit", () => {
  const samples = Array.from({ length: 150 }, (_, index) => sample(index * 10, 270,
    Array.from({ length: 80 }, (_, row) => level(row * 200, row % 101))));
  assert.equal(exports.profileData(satellite(samples), true).cells.length, 12000);
});
