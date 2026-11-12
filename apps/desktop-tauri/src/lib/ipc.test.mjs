import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

// Exercise the actual TS helper, not a second implementation. No WebView or
// production test command is needed; this suite selects the native transport.
const source = ts.transpileModule(readFileSync(new URL("./ipc.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const requestId = "00000000-0000-4000-8000-000000000001";
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function helper(invoke, listen = async () => () => {}, options = {}) {
  const exports = {};
  vm.runInNewContext(source, {
    exports,
    crypto: options.crypto ?? { randomUUID: () => requestId },
    window: options.window,
    require: (name) => {
      if (name === "@tauri-apps/api/core") return { invoke, isTauri: () => options.native !== false };
      if (name === "@tauri-apps/api/event") return { listen };
      if (name === "./browser-transport") return { BrowserTransport: class { constructor() { throw new Error("Unexpected browser transport"); } } };
      throw new Error(`Unexpected dependency ${name}`);
    },
  }, { filename: "ipc.ts" });
  return exports;
}
const plain = (value) => JSON.parse(JSON.stringify(value));

// Flush promise continuation chains without relying on wall-clock sleeps.
const flush = () => new Promise((resolve) => setImmediate(resolve));

test("request handle exposes ID before progress and orders immediate cancel after start ack", async () => {
  const acknowledgement = deferred();
  const completion = deferred();
  const calls = [];
  const api = helper((command, args) => {
    calls.push([command, plain(args)]);
    if (command === "rpc_start") return acknowledgement.promise;
    if (command === "rpc_wait") return completion.promise;
    if (command === "rpc_cancel") return Promise.resolve({ requested: true });
    throw new Error(`Unexpected command ${command}`);
  });
  const handle = api.rpcStart("work", { region: "fixture" });
  assert.equal(handle.requestId, requestId);
  const cancelling = handle.cancel();
  await flush();
  assert.deepEqual(calls, [["rpc_start", { requestId, method: "work", params: { region: "fixture" } }]]);
  acknowledgement.resolve({ requestId, generation: 7 });
  assert.deepEqual(await cancelling, { requested: true });
  assert.deepEqual(calls, [
    ["rpc_start", { requestId, method: "work", params: { region: "fixture" } }],
    ["rpc_wait", { requestId, generation: 7 }],
    ["rpc_cancel", { requestId }],
  ]);
  completion.reject({ code: "cancelled", message: "fixture cancelled" });
  await assert.rejects(handle.result, (err) => err instanceof api.RpcError && err.code === "cancelled" && err.message === "fixture cancelled");
});

test("start failure rejects result and cancel without sending wait or cancel frames", async () => {
  const acknowledgement = deferred();
  const calls = [];
  const api = helper((command) => { calls.push(command); return acknowledgement.promise; });
  const handle = api.rpcStart("work");
  const cancelling = handle.cancel();
  const resultAssertion = assert.rejects(handle.result, (err) => err instanceof api.RpcError && err.code === "engine_exited");
  const cancelAssertion = assert.rejects(cancelling, (err) => err instanceof api.RpcError && err.code === "engine_exited");
  acknowledgement.reject({ code: "engine_exited", message: "quick EOF" });
  await Promise.all([resultAssertion, cancelAssertion]);
  assert.deepEqual(calls, ["rpc_start"]);
});

test("fast terminal result is consumed using the acknowledged generation ticket", async () => {
  const calls = [];
  const api = helper(async (command, args) => {
    calls.push([command, plain(args)]);
    if (command === "rpc_start") return { requestId, generation: 11 };
    if (command === "rpc_wait") return { protocol: 1 };
    throw new Error("unexpected command");
  });
  assert.deepEqual(await api.rpcStart("hello").result, { protocol: 1 });
  assert.deepEqual(calls[1], ["rpc_wait", { requestId, generation: 11 }]);
});

test("compatibility request/cancel/status/restart helpers preserve command names and normalize errors", async () => {
  const calls = [];
  const api = helper(async (command, args) => {
    calls.push([command, args ? plain(args) : undefined]);
    if (command === "rpc_request") throw { code: "operation_failed", message: "fixture failure" };
    if (command === "rpc_cancel") return { requested: false };
    return { running: false, workspace: null, python: null, spawnError: "exited", generation: 2 };
  });
  await assert.rejects(api.rpcRequest("hello"), (err) => err instanceof api.RpcError && err.message === "fixture failure");
  assert.deepEqual(await api.rpcCancel(requestId), { requested: false });
  assert.equal((await api.rpcStatus()).generation, 2);
  assert.equal((await api.rpcRestart()).generation, 2);
  assert.deepEqual(calls.map(([name]) => name), ["rpc_request", "rpc_cancel", "rpc_status", "rpc_restart"]);
});

test("status and progress subscriptions retain reliable event names and payloads", async () => {
  const listeners = new Map();
  const unlisten = () => {};
  const api = helper(async () => {}, async (name, handler) => { listeners.set(name, handler); return unlisten; });
  let status, progress;
  assert.equal(await api.onEngineStatus((value) => { status = value; }), unlisten);
  assert.equal(await api.onEngineProgress((id, event) => { progress = { id, event }; }), unlisten);
  listeners.get("engine-status")({ payload: { running: false, generation: 3 } });
  listeners.get("engine-progress")({ payload: { requestId, event: { percent: 23 } } });
  assert.deepEqual(status, { running: false, generation: 3 });
  assert.deepEqual(progress, { id: requestId, event: { percent: 23 } });
});

test("desktop commands keep their narrow native arguments and normalize failures", async () => {
  const calls = [];
  const api = helper(async (command, args) => {
    calls.push([command, args ? plain(args) : undefined]);
    if (command === "desktop_open_report") throw { code: "invalid_request", message: "outside report root" };
  });
  await assert.rejects(api.openReport("/fixture/report.html"), (error) => error instanceof api.RpcError && error.code === "invalid_request");
  await api.openCache("ecmwf");
  await api.quitDesktop();
  assert.deepEqual(calls, [
    ["desktop_open_report", { path: "/fixture/report.html" }],
    ["desktop_open_cache", { source: "ecmwf" }],
    ["desktop_quit", undefined],
  ]);
});

test("desktop action subscription dispatches the action inside the native payload", async () => {
  let callback;
  const unlisten = () => {};
  const actions = [];
  const api = helper(async () => {}, async (name, handler) => {
    assert.equal(name, "desktop-action");
    callback = handler;
    return unlisten;
  });
  assert.equal(await api.onDesktopAction((action) => actions.push(action)), unlisten);
  callback({ payload: { action: "settings" } });
  callback({ payload: { action: "quit" } });
  assert.deepEqual(actions, ["settings", "quit"]);
});

test("request IDs remain cryptographically random UUIDs when randomUUID is unavailable", async () => {
  let id;
  const api = helper(async (command, args) => {
    if (command === "rpc_start") { id = args.requestId; return { requestId: id, generation: 1 }; }
    if (command === "rpc_wait") return {};
    throw new Error("Unexpected command");
  }, undefined, { crypto: { getRandomValues(bytes) { bytes.fill(0); return bytes; } } });
  await api.rpcStart("hello").result;
  assert.equal(id, "00000000-0000-4000-8000-000000000000");
});

test("browser capabilities reject local control and only open validated service reports", async () => {
  const windows = [];
  const api = helper(() => { throw new Error("Unexpected native call"); }, undefined, {
    native: false, window: { open(...args) { windows.push(args); } },
  });
  assert.equal(api.desktopCapabilities(), false);
  await assert.rejects(api.openCache("gfs"), (error) => error.code === "invalid_request");
  await assert.rejects(api.quitDesktop(), (error) => error.code === "invalid_request");
  await assert.rejects(api.openReport("/server/private/report.html"), (error) => error.code === "invalid_request");
  await assert.rejects(api.openReport("https://example.com/report"), (error) => error.code === "invalid_request");
  await api.openReport(`/api/v1/firesky/reports/${"a".repeat(64)}`);
  assert.deepEqual(windows, [[`/api/v1/firesky/reports/${"a".repeat(64)}`, "_blank", "noopener,noreferrer"]]);
  const unlisten = await api.onDesktopAction(() => { throw new Error("Unexpected desktop action"); });
  unlisten();
});
