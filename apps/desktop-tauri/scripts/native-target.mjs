import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve, join } from "node:path";

export const NATIVE_TARGETS = Object.freeze({
  "aarch64-apple-darwin": { platform: "darwin", arch: "arm64", format: "mach-o", machine: 0x100000c },
  "x86_64-apple-darwin": { platform: "darwin", arch: "x64", format: "mach-o", machine: 0x1000007 },
  "x86_64-pc-windows-msvc": { platform: "win32", arch: "x64", format: "pe", machine: 0x8664 },
  "aarch64-pc-windows-msvc": { platform: "win32", arch: "arm64", format: "pe", machine: 0xaa64 },
});

export function nativeTarget({ env = process.env, platform = process.platform, arch = process.arch } = {}) {
  const explicit = env.FIRESKY_NATIVE_TARGET;
  const tauri = env.TAURI_ENV_TARGET_TRIPLE;
  if (explicit && tauri && explicit !== tauri) throw new Error("FIRESKY_NATIVE_TARGET differs from the Tauri target");
  const triple = explicit || tauri || Object.keys(NATIVE_TARGETS).find((key) => {
    const target = NATIVE_TARGETS[key];
    return target.platform === platform && target.arch === arch;
  });
  if (!NATIVE_TARGETS[triple]) throw new Error(`Unsupported native desktop target: ${triple ?? `${platform}/${arch}`}`);
  return { triple, ...NATIVE_TARGETS[triple] };
}

export function engineBuildPlan(workspace, target, env = process.env) {
  const targetDir = env.CARGO_TARGET_DIR ? resolve(workspace, env.CARGO_TARGET_DIR) : join(workspace, "engine/target");
  const name = target.platform === "win32" ? "firesky-engine.exe" : "firesky-engine";
  return {
    command: "cargo",
    args: ["build", "--locked", "--release", "--target", target.triple, "--manifest-path", join(workspace, "engine/Cargo.toml")],
    binary: join(targetDir, target.triple, "release", name),
    name,
  };
}

function stringAt(bytes, offset, limit = bytes.length) {
  if (offset < 0 || offset >= limit || limit > bytes.length) throw new Error("Invalid native binary string");
  const end = bytes.indexOf(0, offset);
  if (end < offset || end >= limit) throw new Error("Unterminated native binary string");
  return bytes.toString("utf8", offset, end);
}

/** Inspect headers, not filenames or the build host, including PE normal/delay imports. */
export function inspectBinary(bytes) {
  if (bytes.length >= 32 && bytes.readUInt32LE(0) === 0xfeedfacf) {
    const imports = [];
    let minimumOs;
    let platform;
    const count = bytes.readUInt32LE(16), size = bytes.readUInt32LE(20);
    if (32 + size > bytes.length || count > size / 8) throw new Error("Invalid Mach-O load commands");
    let offset = 32;
    for (let index = 0; index < count; ++index) {
      const command = bytes.readUInt32LE(offset), length = bytes.readUInt32LE(offset + 4);
      if (length < 8 || offset + length > 32 + size) throw new Error("Invalid Mach-O load command");
      if ([0xc, 0x80000018, 0x8000001f, 0x20, 0x80000023].includes(command)) {
        if (length < 24) throw new Error("Invalid Mach-O dylib command");
        imports.push(stringAt(bytes, offset + bytes.readUInt32LE(offset + 8), offset + length));
      }
      if (command === 0x32 || command === 0x24) {
        if (length < (command === 0x32 ? 24 : 16)) throw new Error("Invalid Mach-O deployment command");
        platform = command === 0x32 ? bytes.readUInt32LE(offset + 8) : 1;
        const version = bytes.readUInt32LE(offset + (command === 0x32 ? 12 : 8));
        minimumOs = `${version >>> 16}.${(version >>> 8) & 255}.${version & 255}`;
      }
      offset += length;
    }
    return {
      format: "mach-o", machine: bytes.readUInt32LE(4), imports,
      ...(minimumOs ? { minimum_os: minimumOs, platform_id: platform } : {}),
    };
  }
  if (bytes.length >= 64 && bytes.toString("ascii", 0, 2) === "MZ") {
    const pe = bytes.readUInt32LE(0x3c);
    if (pe + 24 > bytes.length || bytes.toString("ascii", pe, pe + 4) !== "PE\0\0") throw new Error("Invalid PE header");
    const sections = bytes.readUInt16LE(pe + 6), optionalSize = bytes.readUInt16LE(pe + 20);
    const optional = pe + 24, table = optional + optionalSize;
    if (optionalSize < 112 || table + sections * 40 > bytes.length || bytes.readUInt16LE(optional) !== 0x20b) {
      throw new Error("Invalid PE32+ header");
    }
    const rvaOffset = (rva) => {
      for (let index = 0; index < sections; ++index) {
        const row = table + index * 40;
        const start = bytes.readUInt32LE(row + 12), rawSize = bytes.readUInt32LE(row + 16);
        if (rva >= start && rva < start + rawSize) {
          const offset = bytes.readUInt32LE(row + 20) + rva - start;
          if (offset < bytes.length) return offset;
        }
      }
      throw new Error("Invalid PE import RVA");
    };
    const imports = [], directoryCount = bytes.readUInt32LE(optional + 108);
    for (const [directory, stride, nameOffset] of [[1, 20, 12], [13, 32, 4]]) {
      if (directory >= directoryCount) continue;
      const entry = optional + 112 + directory * 8;
      if (entry + 8 > table) throw new Error("Invalid PE data directory");
      const rva = bytes.readUInt32LE(entry), length = bytes.readUInt32LE(entry + 4);
      if (!rva) continue;
      if (length < stride || length > bytes.length) throw new Error("Invalid PE imports");
      const begin = rvaOffset(rva);
      let terminated = false;
      for (let offset = begin; offset + stride <= Math.min(bytes.length, begin + length); offset += stride) {
        if (bytes.subarray(offset, offset + stride).every((value) => value === 0)) { terminated = true; break; }
        if (directory === 13 && bytes.readUInt32LE(offset) !== 1) throw new Error("Only RVA-based PE delay imports are supported");
        imports.push(stringAt(bytes, rvaOffset(bytes.readUInt32LE(offset + nameOffset))));
      }
      if (!terminated) throw new Error("Unterminated PE imports");
    }
    return { format: "pe", machine: bytes.readUInt16LE(pe + 4), imports };
  }
  throw new Error("Unsupported native binary (expected thin 64-bit Mach-O or PE32+)");
}

export function assertBinaryTarget(path, target) {
  const identity = inspectBinary(readFileSync(path));
  if (identity.format !== target.format || identity.machine !== target.machine) {
    throw new Error(`Native binary architecture mismatch for ${target.triple}: ${path}`);
  }
  if (target.platform === "darwin" && identity.platform_id !== undefined && identity.platform_id !== 1) {
    throw new Error(`Native binary is not built for macOS: ${path}`);
  }
  return {
    format: identity.format, arch: target.arch, imports: identity.imports,
    ...(identity.minimum_os ? { minimum_os: identity.minimum_os } : {}),
  };
}

export function verifyExternalEngine(path, target, expectedVersion, receipt) {
  const resource = receipt?.schema === "firesky-native-resources-v1";
  const version = resource ? receipt.engine?.version : receipt?.version;
  const checksum = resource ? receipt.files?.find((row) => row.path === receipt.engine?.file)?.sha256 : receipt?.sha256;
  if ((!resource && receipt?.schema !== "firesky-native-engine-v1") || receipt.target !== target.triple || version !== expectedVersion) {
    throw new Error("External engine receipt target/version mismatch");
  }
  const actual = createHash("sha256").update(readFileSync(path)).digest("hex");
  if (checksum !== actual) throw new Error("External engine receipt SHA-256 mismatch");
}
