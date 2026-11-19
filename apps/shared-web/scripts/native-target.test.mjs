import assert from "node:assert/strict";
import { test } from "node:test";
import { lstatSync, mkdtempSync, mkdirSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { copyTreeDereferenced } from "./native-target.mjs";

function walk(root, visit, current = root) {
  visit(current);
  for (const entry of readdirSync(current)) {
    const path = join(current, entry);
    if (lstatSync(path).isDirectory()) walk(root, visit, path);
  }
}

test("copyTreeDereferenced materializes file and directory symlinks on every Node version", () => {
  // Node 22/24 fs.cpSync({dereference:true}) leaves symlinks in the copy;
  // codesign --deep --strict then rejects the macOS bundle. The helper must
  // produce a symlink-free tree regardless of the host Node version.
  const root = mkdtempSync(join(tmpdir(), "firesky-deref-copy-"));
  try {
    const source = join(root, "definitions");
    mkdirSync(join(source, "bufr", "tables", "13"), { recursive: true });
    writeFileSync(join(source, "bufr", "tables", "13", "base.def"), "table 13");
    writeFileSync(join(source, "boot.def"), "boot");
    symlinkSync("13", join(source, "bufr", "tables", "10")); // directory link
    symlinkSync(join("bufr", "tables", "13", "base.def"), join(source, "alias.def")); // file link
    symlinkSync("alias.def", join(source, "chain.def")); // link to a link

    const destination = join(root, "out", "definitions");
    copyTreeDereferenced(source, destination);

    const symlinks = [];
    walk(destination, (path) => { if (lstatSync(path).isSymbolicLink()) symlinks.push(path); });
    assert.deepEqual(symlinks, []);
    assert.equal(readFileSync(join(destination, "bufr", "tables", "10", "base.def"), "utf8"), "table 13");
    assert.equal(readFileSync(join(destination, "alias.def"), "utf8"), "table 13");
    assert.equal(readFileSync(join(destination, "chain.def"), "utf8"), "table 13");
    assert.equal(readFileSync(join(destination, "boot.def"), "utf8"), "boot");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("copyTreeDereferenced fails loudly on dangling symlinks", () => {
  const root = mkdtempSync(join(tmpdir(), "firesky-deref-dangling-"));
  try {
    const source = join(root, "tree");
    mkdirSync(source, { recursive: true });
    symlinkSync("missing.def", join(source, "dangling.def"));
    assert.throws(() => copyTreeDereferenced(source, join(root, "out")), /ENOENT/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
