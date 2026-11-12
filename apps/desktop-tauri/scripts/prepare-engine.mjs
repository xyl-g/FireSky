import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, copyFileSync, chmodSync, existsSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { assertBinaryTarget, engineBuildPlan, nativeTarget, verifyExternalEngine } from "./native-target.mjs";
import { buildResourceManifest, configuredMinimumMacosVersion, MANIFEST_NAME, METADATA_NAME, verifyResourceManifest } from "./native-resources.mjs";

const workspace = fileURLToPath(new URL("../../../", import.meta.url));
function run(command, args, options) {
  const result = spawnSync(command, args, { cwd: options.workspace, env: options.env, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited ${result.status}`);
}

export function prepareEngine({
  root = workspace, env = process.env, platform = process.platform, arch = process.arch,
  resources = fileURLToPath(new URL("../src-tauri/resources/native/", import.meta.url)), execute = run,
} = {}) {
  const target = nativeTarget({ env, platform, arch });
  if (target.platform === "darwin") env = { ...env, MACOSX_DEPLOYMENT_TARGET: configuredMinimumMacosVersion() };
  const plan = engineBuildPlan(root, target, env);
  if (!env.FIRESKY_RUST_ENGINE_BIN) execute(plan.command, plan.args, { workspace: root, env });
  const binary = env.FIRESKY_RUST_ENGINE_BIN ? resolve(root, env.FIRESKY_RUST_ENGINE_BIN) : plan.binary;
  assertBinaryTarget(binary, target);
  const cargo = readFileSync(join(root, "engine/Cargo.toml"), "utf8");
  const version = cargo.match(/^\[package\][\s\S]*?^version\s*=\s*"([^"]+)"/m)?.[1];
  if (env.FIRESKY_RUST_ENGINE_BIN) {
    if (!env.FIRESKY_RUST_ENGINE_MANIFEST) throw new Error("External engine requires FIRESKY_RUST_ENGINE_MANIFEST with target/version/SHA-256 receipt");
    verifyExternalEngine(binary, target, version, JSON.parse(readFileSync(resolve(root, env.FIRESKY_RUST_ENGINE_MANIFEST), "utf8")));
  }
  const parent = dirname(resources);
  mkdirSync(parent, { recursive: true });
  const staging = mkdtempSync(join(parent, ".native-stage-"));
  const backup = `${staging}-previous`;
  try {
    copyFileSync(binary, join(staging, plan.name));
    if (target.platform === "darwin") chmodSync(join(staging, plan.name), 0o755);
    copyFileSync(join(root, "LICENSE"), join(staging, "LICENSE"));
    if (target.platform === "darwin" && platform === "darwin") {
      const decoderEnv = {
        ...env,
        ...(env.FIRESKY_ECCODES_ROOT ? { FIRESKY_ECCODES_PREFIX: resolve(root, env.FIRESKY_ECCODES_ROOT) } : {}),
      };
      execute("bash", [join(root, "scripts/bundle_macos_eccodes.sh"), staging], { workspace: root, env: decoderEnv });
    } else if (env.FIRESKY_ECCODES_ROOT) {
      cpSync(resolve(root, env.FIRESKY_ECCODES_ROOT), join(staging, "eccodes"), { recursive: true, dereference: true });
    } else {
      throw new Error(`Bundled ecCodes is required for ${target.triple}; set FIRESKY_ECCODES_ROOT`);
    }
    const metadataPath = env.FIRESKY_NATIVE_DEPENDENCIES_MANIFEST
      ? resolve(root, env.FIRESKY_NATIVE_DEPENDENCIES_MANIFEST)
      : [join(staging, METADATA_NAME), join(staging, "eccodes", METADATA_NAME)].find(existsSync);
    if (!metadataPath) throw new Error(`Decoder dependency metadata missing; provide ${METADATA_NAME}`);
    const metadata = JSON.parse(readFileSync(metadataPath, "utf8"));
    writeFileSync(join(staging, METADATA_NAME), `${JSON.stringify(metadata, null, 2)}\n`);
    const manifest = buildResourceManifest(staging, target, version, metadata);
    writeFileSync(join(staging, MANIFEST_NAME), `${JSON.stringify(manifest, null, 2)}\n`);
    verifyResourceManifest(staging, target);
    // Never merge into old resources: stale opposite-architecture DLLs must disappear.
    if (existsSync(resources)) renameSync(resources, backup);
    try { renameSync(staging, resources); }
    catch (error) {
      if (existsSync(backup)) renameSync(backup, resources);
      throw error;
    }
    rmSync(backup, { recursive: true, force: true });
    return { resources, target: target.triple, manifest };
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
}

export function prepareAppData({
  root = workspace,
  source = join(root, "resources/app-data"),
  destination = fileURLToPath(new URL("../src-tauri/resources/app-data", import.meta.url)),
} = {}) {
  if (!existsSync(source)) return { skipped: true, source, destination };
  const parent = dirname(destination);
  mkdirSync(parent, { recursive: true });
  const staging = mkdtempSync(join(parent, ".app-data-stage-"));
  const staged = join(staging, "app-data");
  const backup = `${destination}-previous`;
  try {
    cpSync(source, staged, { recursive: true, dereference: true });
    if (!existsSync(join(staged, "RESOURCE-MANIFEST.json"))) {
      throw new Error("Application resource manifest missing");
    }
    if (existsSync(destination)) renameSync(destination, backup);
    try {
      renameSync(staged, destination);
    } catch (error) {
      if (existsSync(backup)) renameSync(backup, destination);
      throw error;
    }
    rmSync(backup, { recursive: true, force: true });
    return { skipped: false, source, destination };
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const prepared = prepareEngine();
    const appData = prepareAppData();
    console.log(`Prepared native resources for ${prepared.target}: ${prepared.resources}`);
    console.log(`Prepared application resources: ${appData.destination}`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}
