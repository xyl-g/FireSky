import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("./browser-transport.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const exports = {};
vm.runInNewContext(source, { exports, Error, Set, Map, Promise, setTimeout, encodeURIComponent }, { filename: "browser-transport.ts" });
const { BrowserTransport } = exports;
const PREFIX = "/api/v1/firesky/desktop";
const plain = (value) => JSON.parse(JSON.stringify(value));
const response = (value, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => value });
const status = (instance_id = "instance-a") => ({ running: true, instance_id, protocol: 1, engine: "firesky-rust", workspace: "/fixture" });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = () => new Promise((resolve) => setImmediate(resolve));
const snapshot = (id, phase, extra = {}) => ({ request_id: id, instance_id: "instance-a", status: phase, progress_seq: 0, ...extra });
function fixture(handler, delay = async () => {}) {
  const calls = [];
  const transport = new BrowserTransport(async (url, init) => {
    const row = { path: url.slice(PREFIX.length), method: init.method, body: init.body ? JSON.parse(init.body) : null };
    calls.push(row);
    assert.equal(init.credentials, "same-origin");
    assert.equal(init.cache, "no-store");
    if (row.path === "/status") return response(status());
    if (row.path.endsWith("/release")) return response({ released: true });
    return handler(row, calls);
  }, delay);
  return { transport, calls };
}

test("HTTP transport preserves native DTOs, stable request identity and same-origin methods", async () => {
  const { transport, calls } = fixture(({ path, body }) => {
    if (path === "/jobs") return response(snapshot(body.request_id, "running"));
    return response(snapshot("one", "done", { result: { datasets: [{ source: "gfs", total_file_count: 0 }] } }));
  });
  const events = [];
  transport.onStatus((value) => events.push(value));
  const result = await transport.start("one", "inventory", {}).result;
  assert.deepEqual(plain(result), { datasets: [{ source: "gfs", total_file_count: 0 }] });
  assert.equal(events[0].generation, 1);
  assert.equal(events[0].engineBinary, null);
  assert.deepEqual(calls.map((call) => call.path), ["/status", "/jobs", "/jobs/one?instance_id=instance-a", "/jobs/one/release"]);
  assert.deepEqual(calls[1].body, { request_id: "one", instance_id: "instance-a", method: "inventory", params: {} });
});

test("immediate cooperative cancellation waits for registration and for the real worker terminal", async () => {
  const ack = deferred(), nextPoll = deferred();
  let terminal = false, settled = false;
  const { transport, calls } = fixture(({ path }) => {
    if (path === "/jobs") return ack.promise;
    if (path.endsWith("/cancel")) return response({ requested: true });
    return response(snapshot("one", terminal ? "cancelled" : "running"));
  }, () => nextPoll.promise);
  const handle = transport.start("one", "prepare", { session: "immutable" });
  const result = handle.result.catch((error) => { settled = true; throw error; });
  void result.catch(() => {});
  const cancelling = handle.cancel();
  await flush();
  assert.equal(calls.some((row) => row.path.endsWith("/cancel")), false);
  ack.resolve(response(snapshot("one", "running")));
  assert.deepEqual(plain(await cancelling), { requested: true });
  await flush();
  assert.equal(settled, false);
  assert.equal(calls.some((row) => row.path.endsWith("/release")), false);
  terminal = true;
  nextPoll.resolve();
  await assert.rejects(result, (error) => error.code === "cancelled");
  assert.equal(calls.some((row) => row.path.endsWith("/release")), true);
});

test("progress includes genuine zero and remains correlated to each request", async () => {
  const { transport } = fixture(({ path, body }) => {
    if (path === "/jobs") return response(snapshot(body.request_id, "running"));
    const id = path.split("/")[2].split("?")[0];
    return response(snapshot(id, "done", { progress_seq: 1, progress: { percent: 0, stage: id }, result: { id } }));
  });
  const events = [];
  const unsubscribe = transport.onProgress((requestId, event) => events.push([requestId, event]));
  await Promise.all([transport.start("one", "check", {}).result, transport.start("two", "check", {}).result]);
  assert.deepEqual(plain(events).sort(), [["one", { percent: 0, stage: "one" }], ["two", { percent: 0, stage: "two" }]]);
  unsubscribe();
  await transport.start("three", "check", {}).result;
  assert.equal(events.length, 2);
});

test("browser reconnect invalidates old handles without restarting the shared service", async () => {
  const nextPoll = deferred();
  const { transport, calls } = fixture(({ path, body }) => path === "/jobs"
    ? response(snapshot(body.request_id, "running")) : response(snapshot("one", "running")), () => nextPoll.promise);
  const handle = transport.start("one", "forecast", { session: "old", confirmed: true });
  await flush();
  const rejection = assert.rejects(handle.result, (error) => error.code === "engine_exited");
  const next = await transport.restart();
  await rejection;
  assert.equal(next.generation, 2);
  await assert.rejects(handle.cancel(), (error) => error.code === "engine_exited");
  assert.equal(calls.some((row) => row.path.includes("restart") || row.path.endsWith("/cancel") || row.path.endsWith("/release")), false);
  nextPoll.resolve();
  await flush();
});

test("instance mismatch is an engine exit, not a successful cancellation", async () => {
  const { transport, calls } = fixture(({ path, body }) => path === "/jobs"
    ? response(snapshot(body.request_id, "running"))
    : response({ error: { code: "engine_exited", message: "service restarted" } }, 409));
  const events = [];
  transport.onStatus((value) => events.push(value));
  await assert.rejects(transport.start("one", "prepare", {}).result, (error) => error.code === "engine_exited");
  assert.equal(events.at(-1).running, false);
  assert.equal(calls.some((row) => row.path.endsWith("/release")), false);
});

test("failed jobs retain structured errors and release registry entries", async () => {
  const { transport, calls } = fixture(({ path, body }) => path === "/jobs"
    ? response(snapshot(body.request_id, "running"))
    : response(snapshot("one", "failed", { error: { code: "invalid_request", message: "fixture invalid date" } })));
  await assert.rejects(transport.start("one", "check", {}).result, (error) => error.code === "invalid_request" && error.message === "fixture invalid date");
  assert.equal(calls.some((row) => row.path.endsWith("/release")), true);
});

test("release failure cannot replace a successful result or invalidate other client state", async () => {
  const calls = [];
  const transport = new BrowserTransport(async (url, init) => {
    calls.push(url);
    if (url.endsWith("/status")) return response(status());
    if (url.endsWith("/release")) throw new Error("fixture release network failure");
    if (url.endsWith("/jobs")) return response(snapshot(JSON.parse(init.body).request_id, "running"));
    return response(snapshot("one", "done", { result: { ready: true } }));
  }, async () => {});
  const events = [];
  transport.onStatus((value) => events.push(value));
  assert.deepEqual(plain(await transport.start("one", "check", {}).result), { ready: true });
  await flush();
  assert.equal(events.at(-1).running, true);
  assert.equal(calls.filter((url) => url.endsWith("/jobs")).length, 1);
});

test("more than 32 short requests release their terminal registry slots", async () => {
  const registered = new Set();
  const { transport } = fixture(({ path, body }) => {
    if (path === "/jobs") {
      assert.ok(registered.size < 32);
      registered.add(body.request_id);
      return response(snapshot(body.request_id, "running"));
    }
    const id = path.split("/")[2].split("?")[0];
    return response(snapshot(id, "done", { result: { id } }));
  });
  // Override release handling so the fixture accounts for successful releases.
  const base = transport.fetcher;
  transport.fetcher = async (url, init) => {
    if (url.endsWith("/release")) registered.delete(url.split("/").at(-2));
    return base(url, init);
  };
  for (let index = 0; index < 40; ++index) await transport.start(`request-${index}`, "hello", {}).result;
  assert.equal(registered.size, 0);
});

test("credential writes and arbitrary methods never reach the service", () => {
  const { transport, calls } = fixture(() => { throw new Error("Unexpected HTTP call"); });
  assert.throws(() => transport.start("one", "save_cams_key", { key: "fixture-never-sent" }), (error) => error.code === "invalid_request");
  assert.throws(() => transport.start("two", "../../admin", {}), (error) => error.code === "invalid_request");
  assert.deepEqual(calls, []);
});

for (const method of ["profile_query", "task_history", "task_events", "task_forget"]) {
  test(`${method} shares the standard browser job transport`, async () => {
    const { transport, calls } = fixture(({ path, body }) => path === "/jobs"
      ? response(snapshot(body.request_id, "running"))
      : response(snapshot("one", "done", { result: { status: "ready" } })));
    await transport.start("one", method, { fixture: 0 }).result;
    assert.equal(calls.find((call) => call.path === "/jobs").body.method, method);
    assert.deepEqual(calls.find((call) => call.path === "/jobs").body.params, { fixture: 0 });
  });
}

test("malformed identity is not accepted as a forecast result", async () => {
  const { transport, calls } = fixture(({ path, body }) => path === "/jobs"
    ? response(snapshot(body.request_id, "running"))
    : response(snapshot("different-request", "done", { result: { ready: true } })));
  await assert.rejects(transport.start("one", "forecast", {}).result, (error) => error.code === "operation_failed");
  assert.equal(calls.some((row) => row.path.endsWith("/release")), false);
});

test("regressing progress sequences are rejected without releasing unfinished jobs", async () => {
  let polls = 0;
  const { transport, calls } = fixture(({ path, body }) => {
    if (path === "/jobs") return response(snapshot(body.request_id, "running"));
    return response(snapshot("one", ++polls === 1 ? "running" : "done", {
      progress_seq: polls === 1 ? 2 : 1,
      result: {},
    }));
  });
  await assert.rejects(transport.start("one", "check", {}).result, (error) => error.code === "operation_failed");
  assert.equal(calls.some((row) => row.path.endsWith("/release")), false);
});

for (const kind of ["status", "start", "poll", "cancel"]) {
  for (const failure of ["network", "engine_exited"]) {
    test(`late ${kind} ${failure} cannot disconnect a reconnected browser or its new jobs`, async () => {
      const oldResponse = deferred(), newResponse = deferred();
      let statusCalls = 0;
      const transport = new BrowserTransport(async (url, init) => {
        const path = url.slice(PREFIX.length);
        const body = init.body ? JSON.parse(init.body) : {};
        if (path === "/status") {
          if (++statusCalls === 1 && kind === "status") return oldResponse.promise;
          return response(status());
        }
        if (path === "/jobs") {
          if (body.request_id === "old" && kind === "start") return oldResponse.promise;
          return response(snapshot(body.request_id, "running"));
        }
        if (path.endsWith("/cancel")) return oldResponse.promise;
        if (path.endsWith("/release")) return response({ released: true });
        if (path.includes("/new?")) return newResponse.promise;
        if (kind === "poll") return oldResponse.promise;
        return new Promise(() => {});
      }, async () => {});
      const events = [];
      transport.onStatus((value) => events.push(value));
      let oldRequest;
      if (kind === "status") oldRequest = transport.status();
      else {
        const old = transport.start("old", "check", {});
        oldRequest = old.result;
        if (kind === "cancel") {
          await flush();
          oldRequest = old.cancel();
        }
      }
      void oldRequest.catch(() => {});
      await flush();
      await transport.restart();
      const next = transport.start("new", "check", {});
      await flush();
      if (failure === "network") oldResponse.reject(new Error("old connection failed"));
      else oldResponse.resolve(response({ error: { code: "engine_exited", message: "old service" } }, 409));
      await flush();
      assert.equal(events.at(-1).running, true);
      assert.equal(events.at(-1).generation, kind === "status" ? 1 : 2);
      newResponse.resolve(response(snapshot("new", "done", { result: { ready: true } })));
      assert.deepEqual(plain(await next.result), { ready: true });
      await assert.rejects(oldRequest);
    });
  }
}
