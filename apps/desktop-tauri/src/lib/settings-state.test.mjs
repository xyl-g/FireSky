import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("./settings-state.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText;
const { camsConfiguration, camsDescription } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
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
