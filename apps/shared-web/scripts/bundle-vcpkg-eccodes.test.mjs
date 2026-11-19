import assert from "node:assert/strict";
import { test } from "node:test";
import { lstatSync, mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { bundleVcpkgEccodes, installedPackages } from "./bundle-vcpkg-eccodes.mjs";

const status = (name, arch, extra = "") => `Package: ${name}\nVersion: installed-1\nArchitecture: ${arch}\nStatus: install ok installed\n${extra}\n`;
function put(path, text) { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, text); }

test("vcpkg status selects installed core packages only and preserves actual port revisions", () => {
  const input = [status("eccodes", "arm64-windows", "Port-Version: 2\n"),
    status("eccodes", "arm64-windows", "Feature: png\n"), status("other", "x64-windows"),
    status("build-tool", "arm64-windows").replace("install ok installed", "deinstall ok config-files")].join("\n");
  assert.deepEqual(installedPackages(input, "arm64-windows"), [{ name: "eccodes", version: "installed-1#2" }]);
  assert.throws(() => installedPackages("Package: eccodes\nPackage: other", "arm64-windows"), /Duplicate/);
});

for (const triplet of ["x64-windows", "arm64-windows"]) {
  test(`${triplet} metadata conversion copies only installed release resources and binds source licenses`, () => {
    const root = mkdtempSync(join(tmpdir(), "firesky-vcpkg-contract-"));
    try {
      const installed = join(root, "installed"), ports = join(root, "ports"), output = join(root, "bundle");
      put(join(installed, "vcpkg/status"), `${status("eccodes", triplet)}\n${status("header-only", triplet)}\n${status("build-tool", triplet)}`);
      // host 构建依赖（ecbuild/python3 模式）：license 为 null 且带 DLL，
      // 但不在 eccodes 运行时闭包内，必须整体排除。
      const eccodesManifest = { name: "eccodes", version: "installed-1", license: "Apache-2.0",
        dependencies: ["header-only", { name: "build-tool", host: true }] };
      for (const name of ["eccodes", "header-only"]) {
        put(join(ports, name, "vcpkg.json"), JSON.stringify(name === "eccodes" ? eccodesManifest : { name, version: "installed-1", license: "Apache-2.0" }));
        put(join(installed, triplet, "share", name, "copyright"), `${name} fixture copyright`);
        put(join(installed, "vcpkg/info", `${name}_installed-1_${triplet}.list`),
          `${triplet}/share/${name}/copyright\n${name === "eccodes" ? `${triplet}/bin/eccodes.dll\n${triplet}/debug/bin/eccodes.dll\n` : ""}`);
      }
      put(join(ports, "build-tool/vcpkg.json"), JSON.stringify({ name: "build-tool", version: "installed-1", license: null }));
      put(join(installed, triplet, "share/build-tool/copyright"), "build-tool fixture copyright");
      put(join(installed, "vcpkg/info", `build-tool_installed-1_${triplet}.list`),
        `${triplet}/bin/build-tool.dll\n${triplet}/share/build-tool/copyright\n`);
      put(join(installed, triplet, "bin/build-tool.dll"), "host build tool must not ship");
      put(join(installed, triplet, "bin/eccodes.dll"), "release fixture, not executable");
      put(join(installed, triplet, "debug/bin/eccodes.dll"), "debug must not ship");
      put(join(installed, triplet, "share/eccodes/definitions/boot.def"), "fixture definitions");
      // ecCodes definition trees ship relative symlinks (e.g. wmo/10 -> 13);
      // the bundle must materialize them because Node 22/24 cpSync dereference
      // is broken and signed bundles reject stray symlinks.
      mkdirSync(join(installed, triplet, "share/eccodes/definitions/tables/13"), { recursive: true });
      put(join(installed, triplet, "share/eccodes/definitions/tables/13/base.def"), "fixture table");
      symlinkSync("13", join(installed, triplet, "share/eccodes/definitions/tables/10"));
      put(join(installed, triplet, "share/eccodes/samples/GRIB2.tmpl"), "fixture samples");
      const metadata = bundleVcpkgEccodes({ installed, ports, triplet, output });
      assert.equal(metadata.packages.length, 2);
      assert.ok(metadata.packages.every((item) => item.name !== "build-tool"));
      assert.throws(() => readFileSync(join(output, "bin/build-tool.dll")), /ENOENT/);
      assert.equal(metadata.packages[0].version, "installed-1");
      assert.equal(readFileSync(join(output, "bin/eccodes.dll"), "utf8"), "release fixture, not executable");
      assert.equal(readFileSync(join(output, "share/eccodes/definitions/tables/10/base.def"), "utf8"), "fixture table");
      assert.ok(!lstatSync(join(output, "share/eccodes/definitions/tables/10")).isSymbolicLink());
      assert.throws(() => readFileSync(join(output, "debug/bin/eccodes.dll")), /ENOENT/);
      assert.ok(metadata.packages.every((item) => item.license_files.length === 1));
      put(join(ports, "eccodes/vcpkg.json"), JSON.stringify({ ...eccodesManifest, version: "newer-than-installed" }));
      assert.throws(() => bundleVcpkgEccodes({ installed, ports, triplet, output }), /does not match pinned license metadata/);
      put(join(ports, "eccodes/vcpkg.json"), JSON.stringify(eccodesManifest));
      rmSync(join(installed, triplet, "share/header-only/copyright"));
      assert.throws(() => bundleVcpkgEccodes({ installed, ports, triplet, output }), /copyright file missing/);
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
}
