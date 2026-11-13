import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";
const exports = {};
const mapRegionExports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./map-region.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports: mapRegionExports });
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./workbench-restore.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, {
  exports, Date, Intl, Set,
  require: (specifier) => {
    if (specifier === "./map-region") return mapRegionExports;
    throw new Error(`Unexpected module: ${specifier}`);
  },
});
const { restoredRequest, parseRestoredRequest } = exports;
const payload = { name: "零坐标", lat: 0, lon: 0, elevation_m: 0, timezone_name: "UTC", target_date: "2026-11-11", phenomenon: "dusk" };
test("history restoration copies exact form parameters including genuine zeros", () => {
  const result = restoredRequest("point", "point", payload);
  assert.equal(result.spot.lat, 0);
  assert.equal(result.spot.elevation_m, 0);
  assert.equal(result.spot.metadataReady, true);
  assert.equal(result.targetDate, payload.target_date);
  result.spot.name = "edited";
  assert.equal(payload.name, "零坐标");
});
test("history restoration rejects incompatible views and malformed parameters atomically", () => {
  assert.equal(restoredRequest("point", "nowcast", payload), null);
  for (const patch of [{ target_date: "2026-02-30" }, { target_date: "2026-1-1" }, { lat: 91 }, { lon: NaN }, { elevation_m: -1 }, { timezone_name: "invalid" }, { phenomenon: "sunset" }]) {
    assert.equal(restoredRequest("point", "point", { ...payload, ...patch }), null);
  }
});
test("regional restoration keeps requested regions and validates exact custom bounds", () => {
  const input = { ...payload, regions: ["east-asia", "custom"], custom_bounds: { west: 0, east: 1, south: 0, north: 1 } };
  const result = restoredRequest("regional", "regional", input);
  assert.equal(result.customRegion, true);
  assert.deepEqual(Array.from(result.regions), ["east-asia"]);
  assert.equal(result.bounds.west, 0);
  assert.equal(restoredRequest("regional", "regional", { ...input, custom_bounds: { west: 0, east: 0, south: 0, north: 1 } }), null);
  const wrapped = { ...input, custom_bounds: { west: 170, east: -170, south: -10, north: 10 } };
  const restored = restoredRequest("regional", "regional", JSON.parse(JSON.stringify(wrapped)));
  assert.equal(restored.bounds.west, 170);
  assert.equal(restored.bounds.east, -170);
  const unconfirmed = parseRestoredRequest("regional", { ...input, elevation_m: -1 });
  assert.equal(unconfirmed.spot.elevation_m, -1);
  assert.equal(unconfirmed.spot.metadataReady, false);
});
