import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { smokeHttpRuntime } from "./smoke-http-runtime.mjs";

function fixture({ python = false, asset = true, identity = true, terminal = "done",
  instance = "fixture", store = { tasks: [], total: 0, automatic_restart: false, issues: [] } } = {}) {
  const calls = [];
  const registrations = [];
  let id = "", method = "";
  return {
    calls, registrations,
    fetcher: async (url, init) => {
      const path = url.pathname;
      calls.push(path);
      const json = (value) => new Response(JSON.stringify(value));
      if (path.endsWith("/health")) return json({ status: "ok", engine: "firesky-rust", python_required: false });
      if (path === "/") return new Response(`<title>FireSky</title><script type="module" src="/assets/app.js"></script>`);
      if (path === "/assets/app.js") return new Response(asset ? "/* real shared SPA fixture */".repeat(10) : "<!doctype html>");
      if (path.endsWith("/status")) return json({ running: true, protocol: 1, engine: "firesky-rust", instance_id: instance });
      if (path.endsWith("/jobs")) {
        const request = JSON.parse(init.body);
        registrations.push(request);
        id = request.request_id;
        method = request.method;
        if (method === "check") {
          store.tasks.push({
            task_id: "a".repeat(64), request_id: id, method, status: "failed", event_seq: 2,
            retry_request: request.params, updated_at_utc: "2026-11-11T00:00:00Z",
          });
          store.total += 1;
        }
        return json({ request_id: id, instance_id: instance, status: "running",
          ...(method === "check" ? { task_id: "a".repeat(64) } : {}) });
      }
      if (path.endsWith("/release")) return json({ released: true });
      return json({
        request_id: identity ? id : "other", instance_id: instance, status: method === "check" ? "failed" : terminal,
        result: method === "task_history" ? { schema: "firesky-task-history-v1", ...store }
          : { protocol: 1, engine: "firesky-rust", runtime_python_used: python },
      });
    },
    pause: async () => {},
    attempts: 2,
  };
}

test("container smoke checks the shared SPA and a Python-free real-protocol hello, then releases it", async () => {
  const f = fixture();
  assert.deepEqual(await smokeHttpRuntime("http://localhost:18765", f), {
    spa: true, engine: "firesky-rust", runtime_python_used: false, instance: "fixture",
  });
  assert.ok(f.calls.at(-1).endsWith("/release"));
  assert.equal(f.calls.some((path) => /forecast|prepare|check/.test(path)), false);
});

test("authenticated smoke sends the token only in headers, never URLs or receipts", async () => {
  const token = "0123456789abcdef0123456789abcdef";
  const f = fixture();
  const fetcher = async (url, init) => {
    assert.equal(init.headers.Authorization, `Bearer ${token}`);
    assert.equal(url.toString().includes(token), false);
    return f.fetcher(url, init);
  };
  const result = await smokeHttpRuntime("http://localhost:18765", { ...f, fetcher, token });
  assert.equal(JSON.stringify(result).includes(token), false);
});

test("history smoke seeds only an invalid local check and verifies unchanged failure after a new runtime instance", async () => {
  const store = { tasks: [], total: 0, automatic_restart: false, issues: [] };
  const first = fixture({ store, instance: "first" });
  const seeded = await smokeHttpRuntime("http://localhost:18765", { ...first, history: { mode: "seed" } });
  assert.deepEqual(first.registrations.map((row) => row.method), ["hello", "check", "task_history"]);
  assert.deepEqual(first.registrations[1].params, { kind: "point", payload: { lat: 91, lon: 120, target_date: "invalid" } });
  const second = fixture({ store, instance: "second" });
  const verified = await smokeHttpRuntime("http://localhost:18765", {
    ...second, history: { mode: "verify", receipt: seeded.task_history },
  });
  assert.equal(verified.task_history.task_id, seeded.task_history.task_id);
  assert.deepEqual(second.registrations.map((row) => row.method), ["hello", "task_history"]);
  for (const mutate of [
    () => { store.automatic_restart = true; },
    () => { store.total += 1; },
    () => { store.tasks[0].event_seq += 1; },
    () => { store.tasks[0].status = "running"; },
    () => { store.tasks = []; },
  ]) {
    const snapshot = structuredClone(store);
    mutate();
    await assert.rejects(smokeHttpRuntime("http://localhost:18765", {
      ...fixture({ store, instance: "second" }), history: { mode: "verify", receipt: seeded.task_history },
    }));
    Object.assign(store, snapshot);
  }
  await assert.rejects(smokeHttpRuntime("http://localhost:18765", {
    ...fixture({ store, instance: "first" }), history: { mode: "verify", receipt: seeded.task_history },
  }), /recreation/);
});

test("Docker CI explicitly reuses a state volume across container recreation and both history smoke phases", () => {
  const workflow = readFileSync(new URL("../../../.github/workflows/native-desktop.yml", import.meta.url), "utf8");
  assert.match(workflow, /docker volume create firesky-smoke-state/);
  assert.equal((workflow.match(/-v firesky-smoke-state:\/data\/state/g) ?? []).length, 2);
  assert.match(workflow, /FIRESKY_TASK_HISTORY_DIR/);
  assert.match(workflow, /--history-seed "\$\{RUNNER_TEMP\}\/firesky-history-smoke\.json"/);
  assert.match(workflow, /--history-verify "\$\{RUNNER_TEMP\}\/firesky-history-smoke\.json"/);
  assert.match(workflow, /docker rm -f firesky-smoke[\s\S]*docker run[\s\S]*--history-verify/);
  assert.match(workflow, /docker volume rm -f firesky-smoke-state/);
});

test("container smoke rejects an SPA fallback as JavaScript, Python use, wrong identity, failure and timeout", async () => {
  for (const options of [{ asset: false }, { python: true }, { identity: false }, { terminal: "failed" }, { terminal: "running" }]) {
    await assert.rejects(smokeHttpRuntime("http://localhost:18765", fixture(options)));
  }
});
