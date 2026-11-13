import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import assert from "node:assert/strict";
import { resolveNativeEngine } from "./native-engine.mjs";

const workspace = resolve(process.env.FIRESKY_WORKSPACE || fileURLToPath(new URL("../../../", import.meta.url)));
const binary = resolveNativeEngine(workspace);
const child = spawn(binary, ["desktop", "--workspace", workspace], {
  cwd: workspace,
  env: {
    ...process.env,
  },
  stdio: ["pipe", "pipe", "pipe"],
});

// This smoke invokes hello only: no prepare/forecast/download or credential output.
const limit = 1024 * 1024;
let stdout = Buffer.alloc(0);
let stderrBytes = 0;
let failure;
child.stdout.on("data", (chunk) => {
  if (stdout.length + chunk.length > limit) {
    failure = new Error("RPC stdout exceeded 1 MiB");
    child.kill();
    return;
  }
  stdout = Buffer.concat([stdout, chunk]);
  if (stdout.includes(10)) child.stdin.end();
});
child.stderr.on("data", (chunk) => { stderrBytes += chunk.length; });
child.stdin.on("error", () => { /* close/error below reports the failed child */ });
const timer = setTimeout(() => {
  failure = new Error("RPC hello/EOF smoke exceeded 15 seconds");
  child.kill();
}, 15_000);
try {
  await new Promise((resolveExit, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => {
      if (failure) reject(failure);
      else if (code !== 0) reject(new Error(`RPC exited ${code ?? signal}; stderr bytes=${stderrBytes}`));
      else resolveExit();
    });
    child.stdin.write('{"id":"smoke-hello","method":"hello","params":{}}\n');
  });
  const lines = stdout.toString("utf8").trim().split("\n");
  assert.equal(lines.length, 1, "stdout must contain only one JSON protocol reply");
  const reply = JSON.parse(lines[0]);
  assert.equal(reply.id, "smoke-hello");
  assert.equal(reply.type, "result");
  assert.equal(reply.result.protocol, 1);
  assert.equal(reply.result.engine, "firesky-rust");
  assert.equal(reply.result.runtime_python_used, false);
  assert.ok(Array.isArray(reply.result.regions) && reply.result.regions.length > 0);
  assert.ok(reply.result.regions.some((region) => region.code === "east-asia"));
  console.log(`RPC smoke OK: protocol=1 engine=firesky-rust regions=${reply.result.regions.length}; EOF exit=0`);
} finally {
  clearTimeout(timer);
  if (child.exitCode === null && child.signalCode === null) child.kill();
}
