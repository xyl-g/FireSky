import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const read = (path) => readFileSync(resolve(root, path), "utf8");
const json = (path) => JSON.parse(read(path));

export function versionMismatches(expected, entries) {
  return Object.entries(entries)
    .filter(([, value]) => value !== expected)
    .map(([name, value]) => `${name}: ${String(value)} (expected ${expected})`);
}

function packageVersion(text, heading, packageName) {
  const blocks = text.split(heading === "package" ? /\[\[package\]\]/ : /\[package\]/);
  const block = blocks.slice(1).find((value) =>
    new RegExp(`^name\\s*=\\s*"${packageName}"\\s*$`, "m").test(value));
  return block?.match(/^version\s*=\s*"([^"]+)"\s*$/m)?.[1];
}

export function checkVersions() {
  const config = json("src-tauri/tauri.conf.json");
  const pkg = json("package.json");
  const lock = json("package-lock.json");
  const errors = versionMismatches(config.version, {
    "package.json": pkg.version,
    "package-lock.json": lock.version,
    "package-lock.json packages[root]": lock.packages?.[""]?.version,
    "Cargo.toml": packageVersion(read("src-tauri/Cargo.toml"), "manifest", "firesky-desktop"),
    "Cargo.lock": packageVersion(read("src-tauri/Cargo.lock"), "package", "firesky-desktop"),
  });
  if (errors.length) throw new Error(`Version drift from tauri.conf.json:\n${errors.join("\n")}`);
  console.log(`Version consistency OK: ${config.version} (tauri.conf.json authority)`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  checkVersions();
}
