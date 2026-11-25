import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { copyEccodesDefinitions } from "./bundle-macos-eccodes.mjs";
import { copyTreeDereferenced } from "./native-target.mjs";
export const METADATA_NAME = "BUNDLE-METADATA.json";

export function installedPackages(text, triplet) {
  const rows = text.trim().split(/\r?\n\r?\n/).map((block) => {
    const fields = {};
    for (const line of block.split(/\r?\n/)) {
      if (!line.trim()) continue;
      if (/^[ \t]/.test(line)) continue;
      const colon = line.indexOf(":");
      if (colon < 1) throw new Error("Malformed vcpkg installed package status");
      const name = line.slice(0, colon);
      if (name in fields) throw new Error("Duplicate vcpkg status field");
      fields[name] = line.slice(colon + 1).trim();
    }
    return fields;
  });
  return rows.filter((row) => row.Architecture === triplet && row.Version && !row.Feature
    && row.Status === "install ok installed").map((row) => {
    if (!/^[a-z0-9][a-z0-9-]*$/.test(row.Package)) throw new Error("Invalid vcpkg package name");
    return { name: row.Package, version: `${row.Version}${row["Port-Version"] ? `#${row["Port-Version"]}` : ""}` };
  });
}

/** Copy installed release DLLs/data and metadata only; never execute package hooks. */
export function bundleVcpkgEccodes({ installed, ports, triplet, output }) {
  if (!["x64-windows", "arm64-windows"].includes(triplet)) throw new Error("Unsupported ecCodes vcpkg triplet");
  const prefix = join(installed, triplet);
  const packages = installedPackages(readFileSync(join(installed, "vcpkg/status"), "utf8"), triplet);
  if (!packages.some((item) => item.name === "eccodes")) throw new Error("ecCodes is not installed for the requested vcpkg triplet");
  // classic 模式 vcpkg 会把 host 构建依赖（ecbuild、python3、vcpkg-cmake*）
  // 装进同一棵 installed 树；它们不属于运行时，ecbuild 的 license 甚至是 null。
  // 只允许 eccodes 的非 host 依赖闭包进入原生包（WinUI 契约禁止 Python 运行时）。
  const installedNames = new Set(packages.map((item) => item.name));
  const runtime = new Set();
  const queue = ["eccodes"];
  while (queue.length) {
    const name = queue.shift();
    if (runtime.has(name) || !installedNames.has(name)) continue;
    runtime.add(name);
    const manifest = JSON.parse(readFileSync(join(ports, name, "vcpkg.json"), "utf8"));
    const deps = [...(manifest.dependencies ?? []),
      ...Object.values(manifest.features ?? {}).flatMap((feature) => feature.dependencies ?? [])];
    for (const dep of deps) {
      if (typeof dep === "string") queue.push(dep);
      else if (dep && typeof dep.name === "string" && dep.host !== true) queue.push(dep.name);
    }
  }
  const lists = readdirSync(join(installed, "vcpkg/info"));
  const metadata = [];
  for (const item of packages) {
    if (!runtime.has(item.name)) continue;
    const manifest = JSON.parse(readFileSync(join(ports, item.name, "vcpkg.json"), "utf8"));
    if (typeof manifest.license !== "string" || !manifest.license.trim()) throw new Error(`vcpkg license declaration missing: ${item.name}`);
    const sourceVersion = manifest.version ?? manifest["version-semver"] ?? manifest["version-date"] ?? manifest["version-string"];
    const sourceIdentity = `${sourceVersion}${manifest["port-version"] ? `#${manifest["port-version"]}` : ""}`;
    if (sourceIdentity !== item.version) {
      throw new Error(`Installed vcpkg version does not match pinned license metadata: ${item.name} ${item.version} != ${sourceIdentity}`);
    }
    const list = lists.filter((name) => name.startsWith(`${item.name}_`) && name.endsWith(`_${triplet}.list`));
    if (list.length !== 1) throw new Error(`vcpkg installed file identity ambiguous: ${item.name}`);
    const files = [];
    for (const name of readFileSync(join(installed, "vcpkg/info", list[0]), "utf8").split(/\r?\n/)) {
      const path = name.startsWith(`${triplet}/`) ? name.slice(triplet.length + 1) : "";
      if (!/^bin\/[^/]+\.dll$/i.test(path)) continue;
      if (!/^[a-z0-9_.-]+$/i.test(path.slice(4))) throw new Error("Unsafe vcpkg DLL filename");
      mkdirSync(join(output, "bin"), { recursive: true });
      copyFileSync(join(prefix, path), join(output, path));
      files.push(`eccodes/${path}`);
    }
    const license = join(prefix, "share", item.name, "copyright");
    if (!existsSync(license) || !readFileSync(license).length) throw new Error(`Installed vcpkg copyright file missing: ${item.name}`);
    const licensePath = `share/${item.name}/copyright`;
    mkdirSync(dirname(join(output, licensePath)), { recursive: true });
    copyFileSync(license, join(output, licensePath));
    files.push(`eccodes/${licensePath}`);
    if (item.name === "eccodes") {
      for (const name of ["definitions", "samples"]) {
        const path = join(prefix, "share/eccodes", name);
        if (!existsSync(path)) throw new Error(`Installed vcpkg ecCodes ${name} missing`);
        const destination = join(output, "share/eccodes", name);
        if (name === "definitions") copyEccodesDefinitions(path, destination);
        else copyTreeDereferenced(path, destination);
        files.push(`eccodes/share/eccodes/${name}`);
      }
    }
    metadata.push({ ...item, license: manifest.license, files, license_files: [`eccodes/${licensePath}`] });
  }
  if (!existsSync(join(output, "bin/eccodes.dll"))) throw new Error("Installed vcpkg ecCodes DLL missing");
  const result = { schema: "firesky-native-dependencies-v1", packages: metadata };
  writeFileSync(join(output, METADATA_NAME), `${JSON.stringify(result, null, 2)}\n`);
  return result;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [installed, ports, triplet, output] = process.argv.slice(2);
    if (!installed || !ports || !triplet || !output) throw new Error("usage: bundle-vcpkg-eccodes.mjs INSTALLED_DIR PORTS_DIR TRIPLET OUTPUT_DIR");
    bundleVcpkgEccodes({ installed: resolve(installed), ports: resolve(ports), triplet, output: resolve(output) });
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}
