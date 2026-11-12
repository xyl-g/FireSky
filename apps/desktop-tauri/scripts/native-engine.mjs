import { accessSync, constants, statSync } from "node:fs";
import { resolve, join } from "node:path";

function executable(path, platform) {
  try {
    accessSync(path, platform === "win32" ? constants.F_OK : constants.X_OK);
    return statSync(path).isFile();
  } catch { return false; }
}

export function resolveNativeEngine(workspace, {
  env = process.env, platform = process.platform, resourceDir, executableDir,
} = {}) {
  const name = platform === "win32" ? "firesky-engine.exe" : "firesky-engine";
  if (env.FIRESKY_RUST_ENGINE_BIN) {
    const path = resolve(env.FIRESKY_RUST_ENGINE_BIN);
    if (!executable(path, platform)) throw new Error("FIRESKY_RUST_ENGINE_BIN is not executable");
    return path;
  }
  const candidates = [
    resourceDir && join(resourceDir, "native", name),
    resourceDir && join(resourceDir, name),
    executableDir && join(executableDir, "Resources", name),
    executableDir && join(executableDir, name),
    join(workspace, "engine", "target", "release", name),
  ].filter(Boolean);
  const path = candidates.find((candidate) => executable(candidate, platform));
  if (!path) throw new Error("Native Rust engine missing; build it or set FIRESKY_RUST_ENGINE_BIN");
  return resolve(path);
}
