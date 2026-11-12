import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { cpSync, mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { NATIVE_TARGETS, assertBinaryTarget, engineBuildPlan, inspectBinary, nativeTarget, verifyExternalEngine } from "./native-target.mjs";
import { buildResourceManifest, MANIFEST_NAME, METADATA_NAME, verifyResourceManifest } from "./native-resources.mjs";
import { prepareAppData, prepareEngine } from "./prepare-engine.mjs";
import { brewPackageFor, bundleMacosEccodes } from "./bundle-macos-eccodes.mjs";

const target = (triple) => ({ triple, ...NATIVE_TARGETS[triple] });
const macArm = target("aarch64-apple-darwin");
const windowsArm = target("aarch64-pc-windows-msvc");
function put(path, bytes) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, bytes);
}
function scratch(work) {
  const root = mkdtempSync(join(tmpdir(), "firesky-native-contract-"));
  try { return work(root); }
  finally { rmSync(root, { recursive: true, force: true }); }
}
// Header-only fixtures verify packaging checks; they are not executable acceptance binaries.
function binary(target, imports = [], minimumOs = "12.0.0") {
  if (target.format === "mach-o") {
    const commands = imports.map((name) => {
      const bytes = Buffer.alloc(Math.ceil((25 + Buffer.byteLength(name)) / 8) * 8);
      bytes.writeUInt32LE(0xc, 0);
      bytes.writeUInt32LE(bytes.length, 4);
      bytes.writeUInt32LE(24, 8);
      bytes.write(name, 24);
      return bytes;
    });
    if (minimumOs) {
      const command = Buffer.alloc(24);
      command.writeUInt32LE(0x32, 0);
      command.writeUInt32LE(24, 4);
      command.writeUInt32LE(1, 8);
      const [major, minor, patch] = minimumOs.split(".").map(Number);
      command.writeUInt32LE((major << 16) | ((minor ?? 0) << 8) | (patch ?? 0), 12);
      commands.push(command);
    }
    const bytes = Buffer.alloc(32);
    bytes.writeUInt32LE(0xfeedfacf, 0);
    bytes.writeUInt32LE(target.machine, 4);
    bytes.writeUInt32LE(commands.length, 16);
    bytes.writeUInt32LE(commands.reduce((size, command) => size + command.length, 0), 20);
    return Buffer.concat([bytes, ...commands]);
  }
  const bytes = Buffer.alloc(0x800);
  bytes.write("MZ");
  bytes.writeUInt32LE(0x80, 0x3c);
  bytes.write("PE\0\0", 0x80);
  bytes.writeUInt16LE(target.machine, 0x84);
  bytes.writeUInt16LE(1, 0x86);
  bytes.writeUInt16LE(240, 0x94);
  bytes.writeUInt16LE(0x20b, 0x98);
  bytes.writeUInt32LE(16, 0x98 + 108);
  const section = 0x98 + 240;
  bytes.writeUInt32LE(0x1000, section + 12);
  bytes.writeUInt32LE(0x400, section + 16);
  bytes.writeUInt32LE(0x400, section + 20);
  if (imports.length) {
    bytes.writeUInt32LE(0x1000, 0x98 + 120);
    bytes.writeUInt32LE((imports.length + 1) * 20, 0x98 + 124);
    let nameOffset = 0x600;
    imports.forEach((name, index) => {
      bytes.writeUInt32LE(0x1000 + nameOffset - 0x400, 0x400 + index * 20 + 12);
      bytes.write(name, nameOffset);
      nameOffset += Buffer.byteLength(name) + 1;
    });
  }
  return bytes;
}
function metadata(target) {
  return {
    schema: "firesky-native-dependencies-v1",
    packages: [{
      name: "ecCodes-fixture", version: "fixture-1", license: "Apache-2.0",
      files: target.platform === "darwin" ? ["lib/libeccodes.dylib", "eccodes"] : ["eccodes"],
      license_files: ["eccodes/LICENSE"],
    }],
  };
}
function decoder(root, target, imports = []) {
  put(join(root, target.platform === "darwin" ? "lib/libeccodes.dylib" : "eccodes/bin/eccodes.dll"), binary(target, imports));
  put(join(root, "eccodes/definitions/boot.def"), "fixture definitions");
  put(join(root, "eccodes/samples/GRIB2.tmpl"), "fixture sample");
  put(join(root, "eccodes/LICENSE"), "fixture license text, not a redistribution grant");
  put(join(root, METADATA_NAME), JSON.stringify(metadata(target)));
}
function complete(root, target, imports = []) {
  decoder(root, target, imports);
  put(join(root, target.platform === "darwin" ? "firesky-engine" : "firesky-engine.exe"), binary(target));
  put(join(root, "LICENSE"), "fixture FireSky license");
}

test("application resources are copied as a complete immutable bundle", () => scratch((root) => {
  const source = join(root, "resources/app-data");
  const destination = join(root, "src-tauri/resources/app-data");
  put(join(source, "RESOURCE-MANIFEST.json"), JSON.stringify({
    schema: "firesky-app-resources-v1",
  }));
  put(join(source, "cities/china-city-catalog.json"), "{}");
  const result = prepareAppData({ root, source, destination });
  assert.equal(result.skipped, false);
  assert.equal(readFileSync(join(destination, "cities/china-city-catalog.json"), "utf8"), "{}");
  assert.ok(statSync(join(destination, "RESOURCE-MANIFEST.json")).isFile());
}));

test("desktop targets cover both architectures, reject ambiguity, and build into the exact Cargo target directory", () => {
  for (const [triple, value] of Object.entries(NATIVE_TARGETS)) {
    assert.equal(nativeTarget({ env: {}, ...value }).triple, triple);
    assert.equal(nativeTarget({ env: { TAURI_ENV_TARGET_TRIPLE: triple }, platform: "linux", arch: "x64" }).triple, triple);
    const plan = engineBuildPlan("/workspace", target(triple), { CARGO_TARGET_DIR: "output" });
    assert.ok(plan.args.includes(triple));
    assert.equal(plan.binary, join("/workspace/output", triple, "release", value.platform === "win32" ? "firesky-engine.exe" : "firesky-engine"));
  }
  assert.throws(() => nativeTarget({ env: { FIRESKY_NATIVE_TARGET: macArm.triple, TAURI_ENV_TARGET_TRIPLE: windowsArm.triple } }), /differs/);
  assert.throws(() => nativeTarget({ env: { FIRESKY_NATIVE_TARGET: "universal-apple-darwin" } }), /Unsupported/);
  assert.throws(() => nativeTarget({ env: {}, platform: "linux", arch: "arm64" }), /Unsupported/);
});

for (const triple of Object.keys(NATIVE_TARGETS)) {
  test(`${triple} manifest validates binary architecture, versions, licenses, and every file checksum`, () => scratch((root) => {
    const t = target(triple);
    complete(root, t);
    const manifest = buildResourceManifest(root, t, "0.1.0", metadata(t));
    assert.equal(manifest.target, triple);
    assert.equal(manifest.files.find((row) => row.path === manifest.engine.file).binary.arch, t.arch);
    assert.equal(manifest.packages[0].version, "fixture-1");
    assert.ok(manifest.files.every((row) => /^[a-f0-9]{64}$/.test(row.sha256)));
    assert.deepEqual(manifest.acceptance, { code_signing: "not_verified", clean_machine: "not_verified", real_data: "not_verified" });
    put(join(root, MANIFEST_NAME), JSON.stringify(manifest));
    assert.deepEqual(verifyResourceManifest(root, t), manifest);
    put(join(root, "eccodes/definitions/boot.def"), "modified after preparation");
    assert.throws(() => verifyResourceManifest(root, t), /checksum/);
  }));
}

test("wrong architecture/platform, fat/unknown/truncated binaries are rejected", () => scratch((root) => {
  const file = join(root, "binary");
  for (const t of Object.keys(NATIVE_TARGETS).map(target)) {
    put(file, binary(t));
    for (const other of Object.keys(NATIVE_TARGETS).map(target)) {
      if (other.triple !== t.triple) assert.throws(() => assertBinaryTarget(file, other), /mismatch/);
    }
  }
  for (const bytes of [Buffer.alloc(0), Buffer.from("not native"), Buffer.from([0xca, 0xfe, 0xba, 0xbe]), Buffer.from("MZ"), binary(macArm).subarray(0, 24)]) {
    assert.throws(() => inspectBinary(bytes));
  }
}));

test("Mach-O manifests preserve minimum deployment OS and reject an ARM64 iOS binary", () => scratch((root) => {
  const path = join(root, "library");
  const bytes = binary(macArm, [], "26.0.0");
  put(path, bytes);
  assert.equal(assertBinaryTarget(path, macArm).minimum_os, "26.0.0");
  bytes.writeUInt32LE(2, 40);
  put(path, bytes);
  assert.throws(() => assertBinaryTarget(path, macArm), /not built for macOS/);
}));

test("macOS gate checks every Mach-O deployment minimum against the actual Tauri configuration", () => scratch((root) => {
  complete(root, macArm);
  const manifest = buildResourceManifest(root, macArm, "0.1.0", metadata(macArm));
  assert.equal(manifest.minimum_system_version, "12.0");
  put(join(root, "firesky-engine"), binary(macArm, [], "12.0.1"));
  put(join(root, "lib/libeccodes.dylib"), binary(macArm, [], "27.2.0"));
  assert.throws(() => buildResourceManifest(root, macArm, "0.1.0", metadata(macArm)),
    (error) => /configured macOS 12\.0/.test(error.message)
      && /firesky-engine: requires macOS 12\.0\.1/.test(error.message)
      && /lib\/libeccodes\.dylib: requires macOS 27\.2\.0/.test(error.message));
  put(join(root, "firesky-engine"), binary(macArm));
  put(join(root, "lib/libeccodes.dylib"), binary(macArm, [], null));
  assert.throws(() => buildResourceManifest(root, macArm, "0.1.0", metadata(macArm)), /lib\/libeccodes\.dylib: deployment minimum missing/);
}));

test("release resources require a decoder, nonempty definitions/samples, package versions and actual license files", () => scratch((root) => {
  complete(root, windowsArm);
  for (const path of ["eccodes/bin/eccodes.dll", "eccodes/definitions/boot.def", "eccodes/samples/GRIB2.tmpl", "eccodes/LICENSE"]) {
    const bytes = readFileSync(join(root, path));
    rmSync(join(root, path));
    assert.throws(() => buildResourceManifest(root, windowsArm, "0.1.0", metadata(windowsArm)), /missing|empty/);
    put(join(root, path), bytes);
  }
  assert.throws(() => buildResourceManifest(root, windowsArm, "0.1.0", {}), /metadata missing/);
  const invalid = metadata(windowsArm);
  invalid.packages[0].version = "";
  assert.throws(() => buildResourceManifest(root, windowsArm, "0.1.0", invalid), /Every dependency/);
  invalid.packages[0].version = "fixture";
  invalid.packages[0].license_files = ["../LICENSE"];
  assert.throws(() => buildResourceManifest(root, windowsArm, "0.1.0", invalid), /Every dependency/);
}));

test("native dylib/DLL imports require a closed local dependency set and all DLLs match the target", () => scratch((root) => {
  complete(root, windowsArm, ["KERNEL32.dll", "api-ms-win-core-file-l1-1-0.dll", "compression.dll"]);
  assert.throws(() => buildResourceManifest(root, windowsArm, "0.1.0", metadata(windowsArm)), /Missing non-system DLL/);
  put(join(root, "eccodes/bin/compression.dll"), binary(windowsArm));
  assert.equal(buildResourceManifest(root, windowsArm, "0.1.0", metadata(windowsArm)).packages.length, 1);
  put(join(root, "eccodes/bin/compression.dll"), binary(target("x86_64-pc-windows-msvc")));
  assert.throws(() => buildResourceManifest(root, windowsArm, "0.1.0", metadata(windowsArm)), /mismatch/);
  rmSync(join(root, "eccodes"), { recursive: true });
  rmSync(join(root, "firesky-engine.exe"));
  complete(root, macArm, ["/usr/lib/libSystem.B.dylib", "@loader_path/libextra.dylib"]);
  assert.throws(() => buildResourceManifest(root, macArm, "0.1.0", metadata(macArm)), /Missing native dependency/);
  put(join(root, "lib/libextra.dylib"), binary(macArm));
  const meta = metadata(macArm);
  meta.packages[0].files.push("lib/libextra.dylib");
  assert.equal(buildResourceManifest(root, macArm, "0.1.0", meta).packages.length, 1);
  put(join(root, "lib/libeccodes.dylib"), binary(macArm, ["/opt/homebrew/lib/libextra.dylib"]));
  assert.throws(() => buildResourceManifest(root, macArm, "0.1.0", meta), /Unresolved native dependency/);
}));

test("symlink resources and undeclared native libraries are rejected", { skip: process.platform === "win32" }, () => scratch((root) => {
  complete(root, macArm);
  symlinkSync("LICENSE", join(root, "alias"));
  assert.throws(() => buildResourceManifest(root, macArm, "0.1.0", metadata(macArm)), /symlinks/);
  rmSync(join(root, "alias"));
  put(join(root, "lib/extra.dylib"), binary(macArm));
  assert.throws(() => buildResourceManifest(root, macArm, "0.1.0", metadata(macArm)), /no version\/license owner/);
}));

test("prepare failure leaves previous resources untouched and success replaces rather than merges them", () => scratch((root) => {
  const resources = join(root, "resources/native"), engine = join(root, "engine-fixture"), decoderRoot = join(root, "decoder");
  put(join(root, "engine/Cargo.toml"), '[package]\nname = "firesky-engine"\nversion = "0.1.0"\n');
  put(join(root, "LICENSE"), "fixture project license");
  put(engine, binary(windowsArm));
  put(join(resources, "stale-opposite-architecture.dll"), "old resources");
  mkdirSync(decoderRoot);
  const receipt = join(root, "engine-receipt.json");
  put(receipt, JSON.stringify({
    schema: "firesky-native-engine-v1", target: windowsArm.triple, version: "0.1.0",
    sha256: createHash("sha256").update(readFileSync(engine)).digest("hex"),
  }));
  const env = {
    FIRESKY_RUST_ENGINE_BIN: engine, FIRESKY_RUST_ENGINE_MANIFEST: receipt,
    FIRESKY_NATIVE_TARGET: windowsArm.triple, FIRESKY_ECCODES_ROOT: decoderRoot,
  };
  const options = { root, resources, env, platform: "win32", arch: "x64", execute: () => { throw new Error("must not build an explicit engine"); } };
  assert.throws(() => prepareEngine(options), /metadata missing/);
  assert.equal(readFileSync(join(resources, "stale-opposite-architecture.dll"), "utf8"), "old resources");
  const stage = join(root, "decoder-stage");
  decoder(stage, windowsArm);
  rmSync(decoderRoot, { recursive: true });
  // FIRESKY_ECCODES_ROOT is an install prefix, copied beneath resources/eccodes.
  const decoderFiles = readFileSync(join(stage, METADATA_NAME));
  cpSync(join(stage, "eccodes"), decoderRoot, { recursive: true });
  put(join(decoderRoot, METADATA_NAME), decoderFiles);
  const prepared = prepareEngine(options);
  assert.equal(prepared.target, windowsArm.triple);
  assert.throws(() => readFileSync(join(resources, "stale-opposite-architecture.dll")), /ENOENT/);
  assert.equal(verifyResourceManifest(resources, windowsArm).engine.version, "0.1.0");
}));

test("external engine receipts reject old versions, target substitutions and replaced binaries", () => scratch((root) => {
  const path = join(root, "engine"), bytes = binary(windowsArm);
  put(path, bytes);
  const receipt = {
    schema: "firesky-native-engine-v1", target: windowsArm.triple, version: "0.1.0",
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
  verifyExternalEngine(path, windowsArm, "0.1.0", receipt);
  assert.throws(() => verifyExternalEngine(path, windowsArm, "0.2.0", receipt), /target\/version/);
  assert.throws(() => verifyExternalEngine(path, macArm, "0.1.0", receipt), /target\/version/);
  put(path, Buffer.concat([bytes, Buffer.from("changed")]));
  assert.throws(() => verifyExternalEngine(path, windowsArm, "0.1.0", receipt), /SHA-256/);
}));

for (const triple of Object.keys(NATIVE_TARGETS)) {
  test(`${triple} preparation uses the target-specific Cargo output, not a stale host engine`, () => scratch((root) => {
    const t = target(triple), resources = join(root, "resources/native"), prefix = join(root, "decoder-prefix");
    put(join(root, "engine/Cargo.toml"), '[package]\nname = "firesky-engine"\nversion = "0.1.0"\n');
    put(join(root, "LICENSE"), "fixture project license");
    put(join(root, "engine/target/release", t.platform === "win32" ? "firesky-engine.exe" : "firesky-engine"), "stale host binary");
    const env = { FIRESKY_NATIVE_TARGET: triple, CARGO_TARGET_DIR: "custom-target" };
    if (t.platform === "win32") {
      const temporary = join(root, "temporary");
      decoder(temporary, t);
      cpSync(join(temporary, "eccodes"), prefix, { recursive: true });
      put(join(prefix, METADATA_NAME), readFileSync(join(temporary, METADATA_NAME)));
      env.FIRESKY_ECCODES_ROOT = prefix;
    }
    const commands = [];
    const result = prepareEngine({
      root, resources, env, platform: t.platform, arch: t.arch,
      execute(command, args, options) {
        commands.push({ command, args });
        assert.equal(options.workspace, root);
        if (command === "cargo") {
          assert.ok(args.includes(triple));
          if (t.platform === "darwin") assert.equal(options.env.MACOSX_DEPLOYMENT_TARGET, "12.0");
          put(engineBuildPlan(root, t, env).binary, binary(t));
        } else if (command === "bash") decoder(args[1], t);
        else throw new Error(`Unexpected fixture command: ${command}`);
      },
    });
    assert.equal(commands[0].command, "cargo");
    assert.equal(result.target, triple);
    assert.equal(verifyResourceManifest(resources, t).target, triple);
  }));
}

test("macOS bundler metadata uses the actual installed Cellar version, not the current formula release", () => {
  const source = "/opt/homebrew/Cellar/eccodes/2.50.0/lib/libeccodes.dylib";
  const formulae = [{ name: "eccodes", license: "Apache-2.0", versions: { stable: "future-version" }, installed: [{ version: "2.50.0" }] }];
  assert.deepEqual(brewPackageFor(source, formulae), {
    name: "eccodes", version: "2.50.0", license: "Apache-2.0", prefix: "/opt/homebrew/Cellar/eccodes/2.50.0",
  });
  assert.throws(() => brewPackageFor(source, [{ ...formulae[0], installed: [] }]), /metadata missing/);
  assert.throws(() => brewPackageFor("/custom/lib/libeccodes.dylib", formulae), /outside Homebrew/);
});

test("macOS automatic bundle captures installed versions/licenses and rewrites recursive non-system imports", () => scratch((root) => {
  const prefix = join(root, "Cellar/eccodes/fixture-1"), dependency = join(root, "Cellar/compression/fixture-2");
  const lib = join(prefix, "lib/libeccodes.dylib"), dep = join(dependency, "lib/libcompression.dylib");
  put(lib, binary(macArm, [dep, "/usr/lib/libSystem.B.dylib"]));
  put(dep, binary(macArm));
  put(join(prefix, "share/eccodes/definitions/boot.def"), "fixture");
  put(join(prefix, "share/eccodes/samples/GRIB2.tmpl"), "fixture");
  put(join(prefix, "LICENSE"), "fixture ecCodes license");
  put(join(dependency, "COPYING"), "fixture compression license");
  const commands = [];
  const metadata = bundleMacosEccodes(join(root, "output"), {
    env: { FIRESKY_ECCODES_PREFIX: prefix },
    execute(name, args) {
      commands.push({ name, args });
      if (name === "brew") return JSON.stringify({ formulae: [
        { name: "eccodes", installed: [{ version: "fixture-1" }], license: "Apache-2.0" },
        { name: "compression", installed: [{ version: "fixture-2" }], license: "BSD-3-Clause" },
      ] });
      return "";
    },
  });
  assert.deepEqual(metadata.packages.map((item) => [item.name, item.version]), [["eccodes", "fixture-1"], ["compression", "fixture-2"]]);
  assert.ok(metadata.packages.every((item) => item.license_files.length === 1));
  assert.ok(commands.some((row) => row.name === "install_name_tool" && row.args.includes("@loader_path/libcompression.dylib")));
  assert.equal(commands.filter((row) => row.name === "codesign").length, 2);
}));
