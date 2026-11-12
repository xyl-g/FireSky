// Run with Node >=22.18: node --test apps/desktop-tauri/src/lib/map-grid.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { DEVELOPMENT_GRID, findGridCell, gridBounds, gridCellText, gridColor, gridSpacing, sanitizeGrid, validCoordinates } from "./map-grid.ts";

test("grid sanitation preserves exact centers, order, zeros and missing values", () => {
  assert.deepEqual(sanitizeGrid([
    { lat: "30.123456789", lon: "120.987654321", peak_quality_index: "0.72" },
    [30, 120, 0], [30.25, 120, null], [30.5, 120, Infinity],
    { lat: 31, lon: 120 }, [31.25, 120, NaN],
  ]), [[30.123456789, 120.987654321, 0.72], [30, 120, 0], [30.25, 120, null], [30.5, 120, null], [31, 120, null], [31.25, 120, null]]);
});

test("grid sanitation rejects non-finite, absent and impossible coordinates", () => {
  assert.deepEqual(sanitizeGrid([
    [NaN, 120, 1], [30, Infinity, 1], [null, 120, 1], ["", 120, 1],
    [false, 120, 1], [91, 120, 1], [30, 181, 1], {}, null, [0, 0, 0],
  ]), [[0, 0, 0]]);
  assert.deepEqual(sanitizeGrid(null), []);
  assert.equal(validCoordinates(0, 0), true);
  assert.equal(validCoordinates(Infinity, 0), false);
});

test("all cells retained without top-N truncation or interpolation", () => {
  const input = Array.from({ length: 10001 }, (_, i) => [30 + i / 100000, 120.123456789, i / 10000]);
  assert.deepEqual(sanitizeGrid(input), input);
});

test("zero and no-data have distinct colors and inspection labels", () => {
  assert.notEqual(gridColor(0), gridColor(null));
  assert.match(gridCellText([0, 0, 0]), /峰值 0\.000/);
  assert.match(gridCellText([0, 0, null]), /缺测 \/ 计算失败/);
  assert.equal(gridColor(0.05), "#d6b56a");
  assert.equal(gridColor(0.4), "#ea7c2c");
  assert.equal(gridColor(1), "#c0261b");
});

test("selection uses returned centers and spacing, including zero/null cells", () => {
  assert.deepEqual(findGridCell(DEVELOPMENT_GRID, 0.25, 30.25, 120.25), [30.25, 120.25, 0]);
  assert.deepEqual(findGridCell(DEVELOPMENT_GRID, 0.25, 30.25, 120.5), [30.25, 120.5, null]);
  assert.equal(findGridCell(DEVELOPMENT_GRID, 0.25, 30.25, 121), undefined);
  assert.equal(findGridCell(DEVELOPMENT_GRID, 0.25, NaN, 120), undefined);
});

test("grid bounds include half cell; empty grids and invalid spacing are safe", () => {
  assert.deepEqual(gridBounds(DEVELOPMENT_GRID, 0.25), [[30.125, 119.875], [30.375, 120.625]]);
  assert.equal(gridBounds([], 0.25), null);
  assert.equal(gridBounds([[10, 179.9, 1], [10, -179.9, 1]], 0.25), null);
  assert.deepEqual(gridBounds([[90, 0, null]], 0.25), [[85.05, -0.125], [85.05, 0.125]]);
  for (const bad of [0, -1, NaN, Infinity, 200]) assert.equal(gridSpacing(bad), 0.25);
  assert.equal(gridSpacing(0.5), 0.5);
});
