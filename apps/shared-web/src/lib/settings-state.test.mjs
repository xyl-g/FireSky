import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("./settings-state.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText;
const {
  camsConfiguration, camsDescription,
  ELEVATION_SOURCE_CHOICES, ELEVATION_SOURCE_STORAGE_KEY,
  parseElevationSource, loadElevationSource, saveElevationSource,
  withElevationSource, elevationSourceDescription, metadataSourceTitle,
} = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const status = {
  configured: true, state: "configured", path: "/fixture/.cdsapirc",
  environment_key: false, can_save: true,
};

test("CAMS configuration retains only public fields and never guesses Boolean status", () => {
  const result = camsConfiguration({ ...status, key: "fixture-secret" });
  assert.deepEqual(result, {
    configured: true, state: "configured", path: "/fixture/.cdsapirc",
    environmentKey: false, canSave: true,
  });
  assert.equal(JSON.stringify(result).includes("fixture-secret"), false);
  for (const patch of [{ configured: "true" }, { can_save: 1 }, { environment_key: "false" }, { state: "other" }]) {
    assert.throws(() => camsConfiguration({ ...status, ...patch }));
  }
});

test("CAMS descriptions distinguish a local configuration from remote credential validation", () => {
  assert.match(camsDescription(camsConfiguration(status)), /本地 CAMS/);
  assert.match(camsDescription(camsConfiguration({ ...status, environment_key: true, can_save: false })), /环境变量/);
  for (const state of ["missing", "wrong_endpoint", "tls_disabled", "unreadable"]) {
    const result = camsConfiguration({ ...status, state });
    assert.equal(result.configured, false);
    assert.doesNotMatch(camsDescription(result), /已配置/);
  }
  assert.match(camsDescription(null), /尚未检查/);
});

test("elevation source accepts exactly the three engine contract values and falls back to auto", () => {
  assert.deepEqual([...ELEVATION_SOURCE_CHOICES], ["auto", "http", "srtm"]);
  for (const value of ELEVATION_SOURCE_CHOICES) assert.equal(parseElevationSource(value), value);
  for (const value of ["", "HTTP", "Auto", "srtm-download", "observer", null, undefined, 1, {}, ["auto"]]) {
    assert.equal(parseElevationSource(value), "auto");
  }
});

test("elevation source persists round-trip and survives corrupt values or unavailable storage", () => {
  const memory = new Map();
  const storage = { getItem: (key) => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) };
  assert.equal(loadElevationSource(storage), "auto");
  for (const value of ELEVATION_SOURCE_CHOICES) {
    assert.equal(saveElevationSource(storage, value), true);
    assert.equal(memory.get(ELEVATION_SOURCE_STORAGE_KEY), value);
    assert.equal(loadElevationSource(storage), value);
  }
  memory.set(ELEVATION_SOURCE_STORAGE_KEY, "bogus");
  assert.equal(loadElevationSource(storage), "auto");
  const broken = { getItem() { throw new Error("storage denied"); }, setItem() { throw new Error("storage denied"); } };
  assert.equal(loadElevationSource(broken), "auto");
  assert.equal(saveElevationSource(broken, "srtm"), false);
});

test("metadata and search params carry elevation_source without mutating the caller payload", () => {
  const metadata = { lat: 30.2741, lon: 120.1551 };
  const search = { query: "杭州" };
  for (const value of ELEVATION_SOURCE_CHOICES) {
    assert.deepEqual(withElevationSource(metadata, value), { lat: 30.2741, lon: 120.1551, elevation_source: value });
    assert.deepEqual(withElevationSource(search, value), { query: "杭州", elevation_source: value });
  }
  assert.deepEqual(metadata, { lat: 30.2741, lon: 120.1551 });
  assert.deepEqual(search, { query: "杭州" });
  assert.deepEqual(withElevationSource(metadata, "bogus"), { lat: 30.2741, lon: 120.1551, elevation_source: "auto" });
});

test("elevation source descriptions distinguish the three modes and response titles map engine provenance only", () => {
  assert.match(elevationSourceDescription("auto"), /优先 open-meteo/);
  assert.match(elevationSourceDescription("auto"), /回退/);
  assert.match(elevationSourceDescription("http"), /仅通过 open-meteo/);
  assert.match(elevationSourceDescription("http"), /不回退/);
  assert.match(elevationSourceDescription("srtm"), /下载地形瓦片/);
  assert.match(elevationSourceDescription("srtm"), /离线可用/);
  assert.match(metadataSourceTitle("open-meteo-point-metadata"), /open-meteo/);
  assert.match(metadataSourceTitle("local-timezone+srtm"), /SRTM/);
  for (const value of ["", "observer", "model-grid", null, undefined]) assert.equal(metadataSourceTitle(value), "");
});

test("workbench call sites, settings panel and app persistence wire the elevation source end to end", async () => {
  const read = (path) => readFile(new URL(path, import.meta.url), "utf8");
  const workbench = await read("./views/WorkbenchView.svelte");
  assert.match(workbench, /"search", withElevationSource\(\{ query: input \}, elevationSource\)/);
  assert.match(workbench, /"metadata", withElevationSource\(\{ lat, lon \}, elevationSource\)/);
  assert.match(workbench, /metadataSourceTitle\(spot\.metadataSource\)/);
  const settings = await read("./views/SettingsView.svelte");
  assert.match(settings, /bind:value=\{elevationSource\}/);
  for (const value of ELEVATION_SOURCE_CHOICES) assert.match(settings, new RegExp(`<option value="${value}">`));
  const app = await read("../App.svelte");
  assert.match(app, /loadElevationSource\(localStorage\)/);
  assert.match(app, /saveElevationSource\(localStorage, elevationSource\)/);
  assert.match(app, /bind:elevationSource/);
  assert.match(app, /\{elevationSource\} \{mapOptions\}/);
});
