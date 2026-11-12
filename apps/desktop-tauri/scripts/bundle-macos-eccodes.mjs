import { spawnSync } from "node:child_process";
import { copyFileSync, cpSync, existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, statSync, writeFileSync, chmodSync } from "node:fs";
import { basename, dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { inspectBinary } from "./native-target.mjs";
import { METADATA_NAME } from "./native-resources.mjs";

function command(name, args) {
  const result = spawnSync(name, args, { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${name} exited ${result.status}: ${result.stderr}`);
  return result.stdout.trim();
}

export function brewPackageFor(source, formulae) {
  const match = source.match(/^(.*\/Cellar\/([^/]+)\/([^/]+))(?:\/|$)/);
  if (!match) throw new Error(`Cannot establish native dependency version outside Homebrew: ${source}; provide FIRESKY_NATIVE_DEPENDENCIES_MANIFEST`);
  const [, prefix, name, version] = match;
  const formula = formulae.find((item) => item.name === name && item.installed?.some((installed) => installed.version === version));
  if (!formula || typeof formula.license !== "string" || !formula.license.trim()) {
    throw new Error(`Homebrew package version or license metadata missing: ${name}/${version}`);
  }
  return { name, version, license: formula.license, prefix };
}

function licenseFiles(prefix, packageName) {
  const found = new Set();
  const walk = (directory, depth) => {
    if (!existsSync(directory)) return;
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (statSync(path).isDirectory()) { if (depth > 0) walk(path, depth - 1); }
      else if (/^(?:licen[cs]e|copying|copyright|notice)(?:[._-].*)?$/i.test(entry.name) && statSync(path).size > 0) found.add(path);
    }
  };
  walk(prefix, 0);
  walk(join(prefix, "share/doc", packageName), 2);
  walk(join(prefix, "share/licenses"), 2);
  return [...found].sort();
}

export function bundleMacosEccodes(resources, { env = process.env, execute = command } = {}) {
  const prefix = env.FIRESKY_ECCODES_PREFIX || (env.FIRESKY_ECCODES_LIBRARY
    ? dirname(dirname(realpathSync(env.FIRESKY_ECCODES_LIBRARY))) : execute("brew", ["--prefix", "eccodes"]));
  const library = env.FIRESKY_ECCODES_LIBRARY || join(prefix, "lib/libeccodes.dylib");
  const copied = new Map();
  mkdirSync(join(resources, "lib"), { recursive: true });
  const copyLibrary = (input) => {
    const source = realpathSync(input), name = basename(input), destination = join(resources, "lib", name);
    if (copied.has(name)) {
      if (copied.get(name) !== source) throw new Error(`Native dependency basename collision: ${name}`);
      return;
    }
    const identity = inspectBinary(readFileSync(source));
    if (identity.format !== "mach-o") throw new Error(`Decoder dependency is not Mach-O: ${source}`);
    copied.set(name, source);
    copyFileSync(source, destination);
    chmodSync(destination, 0o755);
    execute("install_name_tool", ["-id", `@loader_path/${name}`, destination]);
    for (const dependency of identity.imports) {
      if (dependency.startsWith("/usr/lib/") || dependency.startsWith("/System/Library/")) continue;
      let path = dependency;
      if (dependency.startsWith("@loader_path/")) path = join(dirname(source), dependency.slice("@loader_path/".length));
      else if (dependency.startsWith("@rpath/")) path = join(dirname(source), dependency.slice("@rpath/".length));
      if (!path.startsWith("/") || !existsSync(path)) throw new Error(`Unresolved decoder dependency: ${dependency} in ${source}`);
      copyLibrary(path);
      execute("install_name_tool", ["-change", dependency, `@loader_path/${basename(path)}`, destination]);
    }
    // Relocation invalidates dylib signatures. This is ad-hoc, not release signing/notarization.
    execute("codesign", ["--force", "--sign", "-", destination]);
  };
  copyLibrary(library);
  for (const name of ["definitions", "samples"]) {
    const source = join(prefix, "share/eccodes", name);
    if (!existsSync(source)) throw new Error(`ecCodes ${name} missing: ${source}`);
    cpSync(source, join(resources, "eccodes", name), { recursive: true, dereference: true });
  }
  if (env.FIRESKY_NATIVE_DEPENDENCIES_MANIFEST) {
    const metadata = JSON.parse(readFileSync(resolve(env.FIRESKY_NATIVE_DEPENDENCIES_MANIFEST), "utf8"));
    writeFileSync(join(resources, METADATA_NAME), `${JSON.stringify(metadata, null, 2)}\n`);
    return metadata;
  }
  const formulaNames = [...new Set([...copied.values()].map((source) => {
    const name = source.match(/\/Cellar\/([^/]+)\/[^/]+\//)?.[1];
    if (!name) throw new Error(`Cannot identify non-Homebrew native dependency: ${source}; provide FIRESKY_NATIVE_DEPENDENCIES_MANIFEST`);
    return name;
  }))];
  const formulae = JSON.parse(execute("brew", ["info", "--json=v2", ...formulaNames])).formulae;
  const packages = new Map();
  for (const [file, source] of copied) {
    const identity = brewPackageFor(source, formulae);
    let item = packages.get(identity.prefix);
    if (!item) {
      const licenses = licenseFiles(identity.prefix, identity.name);
      if (!licenses.length) throw new Error(`Installed Homebrew license files missing: ${identity.name}`);
      item = { name: identity.name, version: identity.version, license: identity.license, files: [], license_files: [] };
      for (const license of licenses) {
        const path = `ThirdParty/${identity.name}/${relative(identity.prefix, license).split("\\").join("/")}`;
        mkdirSync(dirname(join(resources, path)), { recursive: true });
        copyFileSync(license, join(resources, path));
        item.license_files.push(path);
      }
      item.files.push(`ThirdParty/${identity.name}`);
      packages.set(identity.prefix, item);
    }
    item.files.push(`lib/${file}`);
    if (file === basename(library)) item.files.push("eccodes/definitions", "eccodes/samples");
  }
  const metadata = { schema: "firesky-native-dependencies-v1", packages: [...packages.values()] };
  writeFileSync(join(resources, METADATA_NAME), `${JSON.stringify(metadata, null, 2)}\n`);
  return metadata;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (!process.argv[2]) throw new Error("usage: bundle-macos-eccodes.mjs RESOURCE_DIR");
    bundleMacosEccodes(resolve(process.argv[2]));
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}
