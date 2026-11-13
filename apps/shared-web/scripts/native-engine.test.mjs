import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolveNativeEngine } from "./native-engine.mjs";

test("native launcher selects bundled engine and never falls back from invalid explicit configuration", () => {
  const scratch = mkdtempSync(join(tmpdir(), "firesky-native-launcher-"));
  try {
    const resources = join(scratch, "resources");
    const binary = join(resources, "native", process.platform === "win32" ? "firesky-engine.exe" : "firesky-engine");
    mkdirSync(join(resources, "native"), { recursive: true });
    writeFileSync(binary, "native fixture");
    chmodSync(binary, 0o755);
    assert.equal(resolveNativeEngine(scratch, { resourceDir: resources, env: { FIRESKY_PYTHON: "/unused/python" } }), binary);
    assert.throws(() => resolveNativeEngine(scratch, {
      resourceDir: resources, env: { FIRESKY_RUST_ENGINE_BIN: join(scratch, "missing") },
    }), /not executable/);
    assert.throws(() => resolveNativeEngine(scratch, { env: { FIRESKY_PYTHON: binary } }), /Native Rust engine missing/);
  } finally { rmSync(scratch, { recursive: true, force: true }); }
});
