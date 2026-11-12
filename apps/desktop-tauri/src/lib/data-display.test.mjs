import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./data-display.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports, Date, Set });

test("storage preserves zero and distinguishes missing values", () => {
  assert.equal(exports.formatMB(0), "0.0 MB");
  assert.equal(exports.formatMB(null), "未知");
  assert.equal(exports.formatMB(NaN), "未知");
  assert.equal(exports.formatMB(2048), "2.00 GB");
});

test("local date uses local calendar fields, not UTC slicing", () => {
  assert.equal(exports.localDate(new Date(2026, 10, 11, 0, 1)), "2026-11-11");
});

test("inventory coverage preserves global and zero-valued boundaries", () => {
  assert.equal(exports.cycleCoverage({ source: "gfs" }, { coverage: { 0: [null, [0, 1, 2, 0]] } }), "W0 E1 N2 S0 / 全球");
  assert.equal(exports.cycleCoverage({ source: "ecmwf" }, {}), "全球原始场");
  assert.equal(exports.cycleCoverage({ source: "cams", area_bounds: { tiny: [1, 0, 0, 1] } },
    { area_keys: { 0: ["tiny"] } }), "N1 W0 S0 E1");
  assert.equal(exports.cycleCoverage({ source: "cams" }, {}), "范围未识别");
});

test("only explicitly passed probes are labeled successful", () => {
  assert.equal(exports.probeLabel("passed"), "下载解码通过");
  assert.equal(exports.probeLabel("unexpected"), "未通过");
  assert.equal(exports.initialProbes().length, 3);
});
