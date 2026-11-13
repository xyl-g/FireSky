import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { resolveNativeEngine } from "./native-engine.mjs";

const workspace = resolve(process.env.FIRESKY_WORKSPACE || fileURLToPath(new URL("../../../", import.meta.url)));
const binary = resolveNativeEngine(workspace);
// Fast checks may build a GFS metadata index. Keep even those writes out of the
// user's real caches, while exercising the real sidecar and planning code.
const scratch = await mkdtemp(join(tmpdir(), "firesky-data-rpc-smoke-"));
const child = spawn(binary, ["desktop", "--workspace", scratch], {
  cwd: scratch,
  env: {
    ...process.env,
    FIRESKY_GFS_CACHE_DIR: join(scratch, "gfs"),
    FIRESKY_ECMWF_CACHE_DIR: join(scratch, "ecmwf"),
    FIRESKY_CAMS_CACHE_DIR: join(scratch, "cams"),
  },
  stdio: ["pipe", "pipe", "pipe"],
});

const maxLineBytes = 1024 * 1024;
const decoder = new TextDecoder("utf-8", { fatal: true });
const pending = new Map();
let buffer = Buffer.alloc(0);
let stderrBytes = 0;
let sequence = 0;
let failure;
let forceTimer;
let closed = false;
const childClosed = new Promise((resolveClose) => {
  child.once("close", (code, signal) => {
    closed = true;
    clearTimeout(forceTimer);
    if (buffer.length && !failure) fail(new Error("RPC ended with a truncated stdout frame"));
    if (pending.size && !failure) fail(new Error("RPC exited before completing the data smoke"));
    resolveClose({ code, signal });
  });
});

function stopChild() {
  if (closed) return;
  child.stdin.end();
  child.kill();
  forceTimer ??= setTimeout(() => {
    if (!closed) child.kill("SIGKILL");
  }, 2_000);
}

function fail(error) {
  failure ??= error;
  for (const request of pending.values()) request.reject(failure);
  pending.clear();
  stopChild();
}

child.once("error", () => fail(new Error("Could not start the native Rust RPC sidecar")));
child.stdin.on("error", () => fail(new Error("RPC stdin closed before the smoke completed")));
child.stderr.on("data", (chunk) => { stderrBytes += chunk.length; });
child.stdout.on("data", (chunk) => {
  if (failure) return;
  let offset = 0;
  try {
    while (offset < chunk.length) {
      const newline = chunk.indexOf(10, offset);
      const end = newline < 0 ? chunk.length : newline;
      const part = chunk.subarray(offset, end);
      if (buffer.length + part.length > maxLineBytes) throw new Error("RPC stdout frame exceeded 1 MiB");
      buffer = Buffer.concat([buffer, part]);
      if (newline < 0) break;
      const frame = JSON.parse(decoder.decode(buffer));
      buffer = Buffer.alloc(0);
      offset = newline + 1;
      const request = pending.get(frame.id);
      if (!request) throw new Error("RPC returned an unknown request ID");
      if (frame.type === "progress") continue;
      if (!["result", "error"].includes(frame.type)) throw new Error("RPC returned an unsupported frame type");
      pending.delete(frame.id);
      if (frame.type === "result") request.resolve(frame.result);
      else request.reject(new Error(`RPC ${request.method} returned an error`));
    }
  } catch {
    fail(new Error("RPC stdout violated the bounded JSON-lines contract"));
  }
});

function request(method, params = {}) {
  if (failure) return Promise.reject(failure);
  const id = `data-smoke-${++sequence}`;
  return new Promise((resolveResult, reject) => {
    pending.set(id, { method, resolve: resolveResult, reject });
    child.stdin.write(JSON.stringify({ id, method, params }) + "\n");
  });
}

const timer = setTimeout(() => fail(new Error("RPC data/EOF smoke exceeded 30 seconds")), 30_000);
try {
  const hello = await request("hello");
  assert.equal(hello.protocol, 1);
  assert.equal(hello.engine, "firesky-rust");
  assert.equal(hello.runtime_python_used, false);
  const inventory = await request("inventory");
  assert.ok(Array.isArray(inventory.datasets));
  assert.equal(inventory.datasets.length, 3);
  assert.deepEqual(new Set(inventory.datasets.map((row) => row.source)), new Set(["gfs", "ecmwf", "cams"]));
  for (const row of inventory.datasets) {
    assert.equal(typeof row.path, "string");
    assert.ok(Number.isInteger(row.total_file_count) && row.total_file_count >= 0);
    assert.ok(Number.isFinite(row.total_storage_mb) && row.total_storage_mb >= 0);
    assert.ok(Number.isInteger(row.unindexed_file_count) && row.unindexed_file_count >= 0);
    assert.ok(row.cycles && typeof row.cycles === "object" && !Array.isArray(row.cycles));
  }
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const targetDate = [
    tomorrow.getFullYear(), String(tomorrow.getMonth() + 1).padStart(2, "0"),
    String(tomorrow.getDate()).padStart(2, "0"),
  ].join("-");
  const checked = await request("check", {
    kind: "regional",
    payload: {
      regions: ["custom"],
      custom_bounds: { west: 120, east: 120.25, south: 30, north: 30.25 },
      custom_timezone_name: "Asia/Shanghai",
      target_date: targetDate, phenomenon: "dusk",
      model_source: "both", ecmwf_source: "aws", spacing_deg: 0.25,
      workers: 1, forecast_mode: "model", satellite_source: "off",
    },
  });
  assert.ok(typeof checked.session === "string" && checked.session.length > 0);
  assert.equal(typeof checked.report?.ready, "boolean");
  assert.ok(Array.isArray(checked.report.datasets));
  assert.equal(checked.report.datasets.length, 3);
  assert.deepEqual(new Set(checked.report.datasets.map((row) => row.source)), new Set(["gfs", "ecmwf", "cams"]));
  for (const row of checked.report.datasets) {
    assert.equal(typeof row.ready, "boolean");
    assert.ok(Number.isInteger(row.missing_count) && row.missing_count >= 0);
    assert.equal(row.ready, row.missing_count === 0);
  }
  assert.equal((await request("dismiss", { session: checked.session })).dismissed, true);
  child.stdin.end();
  const result = await childClosed;
  if (failure) throw failure;
  assert.equal(result.code, 0, `RPC exited ${result.code ?? result.signal}; stderr bytes=${stderrBytes}`);
  console.log(`Data RPC smoke OK: isolated caches; inventory=${inventory.datasets.length}; check=${checked.report.datasets.length} datasets; dismissed=true; EOF exit=0`);
  console.log("No publications, network probes, preparation, forecasts, or credential changes were requested.");
} finally {
  clearTimeout(timer);
  if (!closed) stopChild();
  await childClosed;
  clearTimeout(forceTimer);
  await rm(scratch, { recursive: true, force: true });
}
