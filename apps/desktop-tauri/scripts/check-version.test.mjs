import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { versionMismatches, checkVersions } from "./check-version.mjs";

test("desktop versions match the Tauri configuration", () => checkVersions());
test("chosen desktop deployment metadata stays explicit", () => {
  const config = JSON.parse(readFileSync(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
  assert.equal(config.identifier, "cn.firesky.desktop");
  assert.equal(config.bundle.macOS.minimumSystemVersion, "12.0");
  assert.deepEqual(config.bundle.windows.webviewInstallMode, { type: "offlineInstaller" });
});
test("version guard rejects drift and missing metadata", () => {
  assert.deepEqual(versionMismatches("0.1.0", { npm: "0.1.0" }), []);
  assert.equal(versionMismatches("0.1.0", { npm: "0.2.0", cargo: undefined }).length, 2);
});
