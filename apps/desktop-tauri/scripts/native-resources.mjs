import { createHash } from "node:crypto";
import { lstatSync, readdirSync, readFileSync, statSync } from "node:fs";
import { basename, join, posix } from "node:path";
import { assertBinaryTarget } from "./native-target.mjs";

export const METADATA_NAME = "BUNDLE-METADATA.json";
export const MANIFEST_NAME = "RESOURCE-MANIFEST.json";
const text = (value) => typeof value === "string" && value.trim().length > 0 && !/[\r\n\0]/.test(value);
const safePath = (value) => text(value) && !value.includes("\\") && !value.startsWith("/")
  && value.split("/").every((part) => part && part !== "." && part !== "..") && !value.includes(":");
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
export function configuredMinimumMacosVersion() {
  const config = JSON.parse(readFileSync(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
  const minimum = config.bundle?.macOS?.minimumSystemVersion;
  if (typeof minimum !== "string" || !/^\d+\.\d+(?:\.\d+)?$/.test(minimum)) {
    throw new Error("Tauri macOS minimumSystemVersion must be an explicit numeric version");
  }
  return minimum;
}
function exceedsVersion(actual, maximum) {
  const parts = actual.split(".").map(Number), limit = maximum.split(".").map(Number);
  for (let index = 0; index < 3; ++index) {
    if ((parts[index] ?? 0) !== (limit[index] ?? 0)) return (parts[index] ?? 0) > (limit[index] ?? 0);
  }
  return false;
}

export function resourceFiles(root, prefix = "") {
  const files = [];
  for (const name of readdirSync(join(root, prefix)).sort()) {
    const path = prefix ? `${prefix}/${name}` : name;
    if (!safePath(path)) throw new Error(`Unsafe native resource path: ${path}`);
    const info = lstatSync(join(root, path));
    if (info.isSymbolicLink()) throw new Error(`Native resources must not contain symlinks: ${path}`);
    if (info.isDirectory()) files.push(...resourceFiles(root, path));
    else if (info.isFile()) files.push(path);
    else throw new Error(`Native resource must be a regular file: ${path}`);
  }
  return files;
}

const WINDOWS_SYSTEM_DLLS = new Set([
  "advapi32.dll", "avrt.dll", "bcrypt.dll", "bcryptprimitives.dll", "cfgmgr32.dll", "comctl32.dll",
  "comdlg32.dll", "crypt32.dll", "cryptbase.dll", "cryptnet.dll", "d3d11.dll", "dbghelp.dll",
  "dnsapi.dll", "dwmapi.dll", "dxgi.dll", "gdi32.dll", "imm32.dll", "iphlpapi.dll", "kernel32.dll",
  "msvcrt.dll", "ncrypt.dll", "netapi32.dll", "normaliz.dll", "ntdll.dll", "ole32.dll", "oleaut32.dll",
  "opengl32.dll", "powrprof.dll", "propsys.dll", "psapi.dll", "rpcrt4.dll", "secur32.dll",
  "setupapi.dll", "shell32.dll", "shlwapi.dll", "synchronization.dll", "ucrtbase.dll", "user32.dll",
  "userenv.dll", "usp10.dll", "uxtheme.dll", "version.dll", "winhttp.dll", "wininet.dll",
  "winmm.dll", "winspool.drv", "wintrust.dll", "wldap32.dll", "ws2_32.dll", "wtsapi32.dll",
]);

function checkImports(path, identity, files, target) {
  for (const dependency of identity.imports) {
    if (target.platform === "darwin") {
      if (dependency.startsWith("/usr/lib/") || dependency.startsWith("/System/Library/")) continue;
      if (!dependency.startsWith("@loader_path/")) throw new Error(`Unresolved native dependency in ${path}: ${dependency}`);
      const resolved = posix.normalize(posix.join(posix.dirname(path), dependency.slice("@loader_path/".length)));
      if (!safePath(resolved) || !files.includes(resolved)) throw new Error(`Missing native dependency in ${path}: ${dependency}`);
    } else {
      const name = dependency.toLowerCase();
      if (WINDOWS_SYSTEM_DLLS.has(name) || /^(?:api|ext)-ms-win-[a-z0-9-]+\.dll$/.test(name)) continue;
      if (basename(dependency) !== dependency || !files.some((file) => {
        // The launcher adds eccodes/bin (or eccodes itself) to PATH, not arbitrary resource subdirectories.
        return ["", "eccodes", "eccodes/bin"].includes(posix.dirname(file) === "." ? "" : posix.dirname(file))
          && posix.basename(file).toLowerCase() === name;
      })) throw new Error(`Missing non-system DLL in ${path}: ${dependency}`);
    }
  }
}

export function buildResourceManifest(root, target, engineVersion, metadata) {
  if (!text(engineVersion)) throw new Error("Native engine version missing");
  if (metadata?.schema !== "firesky-native-dependencies-v1" || !Array.isArray(metadata.packages) || !metadata.packages.length) {
    throw new Error("Decoder dependency metadata missing; provide BUNDLE-METADATA.json with versions and licenses");
  }
  const files = resourceFiles(root).filter((path) => path !== MANIFEST_NAME);
  const engine = target.platform === "win32" ? "firesky-engine.exe" : "firesky-engine";
  if (!files.includes(engine) || !files.includes("LICENSE")) throw new Error("Native engine or FireSky LICENSE missing");
  const library = target.platform === "darwin" ? "lib/libeccodes.dylib"
    : ["eccodes/bin/eccodes.dll", "eccodes/eccodes.dll"].find((path) => files.includes(path));
  if (!library || !files.includes(library)) throw new Error("Required bundled ecCodes decoder missing");
  for (const name of ["definitions", "samples"]) {
    if (![ `eccodes/${name}/`, `eccodes/share/eccodes/${name}/` ].some((prefix) => files.some((path) => path.startsWith(prefix)))) {
      throw new Error(`Required ecCodes ${name} missing or empty`);
    }
  }
  const packages = metadata.packages.map((item) => {
    if (![item?.name, item?.version, item?.license].every(text) || !Array.isArray(item.files)
      || !item.files.length || !item.files.every(safePath) || !Array.isArray(item.license_files)
      || !item.license_files.length || !item.license_files.every(safePath)) {
      throw new Error("Every dependency needs a name, version, license, files and license_files");
    }
    for (const license of item.license_files) {
      if (!files.includes(license) || statSync(join(root, license)).size === 0) throw new Error(`Dependency license missing: ${license}`);
    }
    for (const path of item.files) {
      if (!files.some((file) => file === path || file.startsWith(`${path}/`))) throw new Error(`Declared dependency resource missing: ${path}`);
    }
    return { name: item.name, version: item.version, license: item.license, files: item.files, license_files: item.license_files };
  });
  const minimumSystemVersion = target.platform === "darwin" ? configuredMinimumMacosVersion() : undefined;
  const incompatible = [];
  const records = files.map((path) => {
    const bytes = readFileSync(join(root, path));
    const owners = packages.filter((item) => item.files.some((entry) => path === entry || path.startsWith(`${entry}/`)));
    if (owners.length > 1) throw new Error(`Ambiguous dependency ownership: ${path}`);
    const binary = path === engine || /\.dll$/i.test(path) || /\.dylib$/.test(path);
    let identity;
    if (binary) {
      identity = assertBinaryTarget(join(root, path), target);
      if (minimumSystemVersion && (!identity.minimum_os || exceedsVersion(identity.minimum_os, minimumSystemVersion))) {
        incompatible.push(`${path}: ${identity.minimum_os ? `requires macOS ${identity.minimum_os}` : "deployment minimum missing"}`);
      }
      checkImports(path, identity, files, target);
      if (path !== engine && owners.length !== 1) throw new Error(`Native library has no version/license owner: ${path}`);
    }
    return {
      path, size: bytes.length, sha256: digest(bytes),
      ...(identity ? { binary: identity } : {}),
      ...(path === engine ? { package: "firesky-engine", version: engineVersion, license_file: "LICENSE" }
        : owners.length ? { package: owners[0].name, version: owners[0].version } : {}),
    };
  });
  if (incompatible.length) {
    throw new Error(`Bundled Mach-O files do not support configured macOS ${minimumSystemVersion}:\n${incompatible.join("\n")}\nUse decoder/engine builds targeting macOS ${minimumSystemVersion} or earlier.`);
  }
  if (!packages.some((item) => item.files.some((entry) => library === entry || library.startsWith(`${entry}/`)))) {
    throw new Error("ecCodes decoder has no package metadata");
  }
  return {
    schema: "firesky-native-resources-v1", target: target.triple, platform: target.platform, arch: target.arch,
    ...(minimumSystemVersion ? { minimum_system_version: minimumSystemVersion } : {}),
    engine: { name: "firesky-engine", version: engineVersion, file: engine, license_file: "LICENSE" },
    packages, files: records,
    acceptance: { code_signing: "not_verified", clean_machine: "not_verified", real_data: "not_verified" },
  };
}

export function verifyResourceManifest(root, target) {
  const stored = JSON.parse(readFileSync(join(root, MANIFEST_NAME), "utf8"));
  if (stored.target !== target.triple) throw new Error("Native resource manifest target mismatch");
  const actual = buildResourceManifest(root, target, stored.engine?.version, {
    schema: "firesky-native-dependencies-v1", packages: stored.packages,
  });
  // The publication itself is deliberately excluded from its own checksums.
  if (JSON.stringify(actual) !== JSON.stringify(stored)) throw new Error("Native resource manifest checksum or metadata mismatch");
  return stored;
}
