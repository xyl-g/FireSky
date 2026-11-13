import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("./task-history.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const exports = {};
vm.runInNewContext(source, { exports, structuredClone, Date, Set, Error }, { filename: "task-history.ts" });
const { TaskHistoryController, taskStatus } = exports;
const plain = (value) => JSON.parse(JSON.stringify(value));
const first = "a".repeat(64), second = "b".repeat(64);
function record(task_id = first, status = "interrupted") {
  return {
    task_id, method: "forecast", status, created_at_utc: "2026-11-11T09:00:00Z",
    updated_at_utc: "2026-11-11T09:01:00Z", cancel_requested: false, event_seq: 2,
    retry_request: { kind: "point", payload: { name: "杭州", lat: 30, lon: 120, target_date: "2026-11-11" } },
    progress: { stage: "assessment", percent: 0 },
  };
}
const response = (...tasks) => ({ schema: "firesky-task-history-v1", tasks, total: tasks.length, issues: [], automatic_restart: false });
function fixture() {
  const requests = [], updates = [];
  const controller = new TaskHistoryController({
    request(method, params) {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      requests.push({ method, params, resolve, reject });
      return promise;
    },
  }, (state) => updates.push(state));
  return { controller, requests, updates };
}
async function load(f, ...tasks) {
  const promise = f.controller.refresh();
  f.requests.at(-1).resolve(response(...tasks));
  await promise;
}

test("history is read-only, retains zero progress, and restore clones parameters without starting work", async () => {
  const f = fixture();
  await load(f, record());
  assert.equal(f.controller.state.tasks[0].progress.percent, 0);
  assert.equal(taskStatus("interrupted"), "已中断");
  const retry = f.controller.retry(first);
  retry.payload.name = "changed";
  assert.equal(f.controller.retry(first).payload.name, "杭州");
  assert.deepEqual(f.requests.map((item) => item.method), ["task_history"]);
});

test("a new generation ignores a late list response or failure", async () => {
  for (const failure of [false, true]) {
    const f = fixture();
    const old = f.controller.refresh();
    f.controller.reset();
    await load(f, record(second));
    if (failure) f.requests[0].reject(new Error("old engine exited"));
    else f.requests[0].resolve(response(record(first)));
    await old;
    assert.equal(f.controller.state.tasks[0].task_id, second);
    assert.equal(f.controller.state.error, "");
    assert.equal(f.controller.state.loading, false);
  }
});

test("unknown protocol and duplicate task IDs cannot replace a verified list", async () => {
  const f = fixture();
  await load(f, record());
  for (const value of [
    { ...response(record(second)), automatic_restart: true },
    response(record(second), record(second)),
    response({ ...record(second), status: "unknown" }),
  ]) {
    const refreshing = f.controller.refresh();
    f.requests.at(-1).resolve(value);
    await refreshing;
    assert.equal(f.controller.state.tasks[0].task_id, first);
    assert.notEqual(f.controller.state.error, "");
  }
});

test("running tasks cannot restore or forget and cancellation remains running", async () => {
  const f = fixture();
  const running = { ...record(first, "running"), cancel_requested: true };
  await load(f, running);
  assert.equal(f.controller.retry(first), null);
  assert.equal(await f.controller.forget(first), false);
  assert.equal(f.requests.length, 1);
  assert.equal(taskStatus("running", true), "取消中");
});

test("event replay is scoped to selected task and late response never replaces a newer selection", async () => {
  const f = fixture();
  await load(f, record(), record(second));
  const old = f.controller.select(first);
  const latest = f.controller.select(second);
  f.requests[2].resolve({ task_id: second, events: [{ seq: 7, at_utc: "2026-11-11T09:00:00Z", event: "interrupted", data: {} }], next_seq: 7, truncated: true });
  await latest;
  f.requests[1].resolve({ task_id: first, events: [], next_seq: 0, truncated: false });
  await old;
  assert.equal(f.controller.state.selectedId, second);
  assert.equal(f.controller.state.events[0].seq, 7);
  assert.equal(f.controller.state.truncated, true);
});

test("invalid event identities and sequences are rejected, and closing invalidates pending replay", async () => {
  const f = fixture();
  await load(f, record());
  for (const value of [
    { task_id: second, events: [], next_seq: 0, truncated: false },
    { task_id: first, events: [{ seq: 2, at_utc: "2026-11-11T09:00:00Z" }], next_seq: 1, truncated: false },
  ]) {
    const selecting = f.controller.select(first);
    f.requests.at(-1).resolve(value);
    await selecting;
    assert.notEqual(f.controller.state.eventsError, "");
  }
  const selecting = f.controller.select(first);
  f.controller.closeEvents();
  f.requests.at(-1).reject(new Error("late"));
  await selecting;
  assert.equal(f.controller.state.selectedId, null);
  assert.equal(f.controller.state.eventsError, "");
});

test("list refresh replays newly appended events and closes a selection removed by another client", async () => {
  const f = fixture();
  await load(f, record(first, "running"));
  const opening = f.controller.select(first);
  f.requests.at(-1).resolve({ task_id: first, events: [], next_seq: 2, truncated: false });
  await opening;
  await load(f, { ...record(first, "done"), event_seq: 3 });
  assert.equal(f.requests.at(-1).method, "task_events");
  f.requests.at(-1).resolve({ task_id: first, events: [{ seq: 3, at_utc: "2026-11-11T09:01:00Z", event: "done", data: {} }], next_seq: 3, truncated: false });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.controller.state.events[0].event, "done");
  await load(f);
  assert.equal(f.controller.state.selectedId, null);
  assert.equal(f.controller.state.events.length, 0);
});

test("forget invalidates pending list, closes events and never deletes forecast artifacts", async () => {
  const f = fixture();
  await load(f, record());
  const listing = f.controller.refresh();
  const forgetting = f.controller.forget(first);
  assert.equal(f.controller.state.forgetting, first);
  f.requests[2].resolve({ forgotten: true });
  assert.equal(await forgetting, true);
  f.requests[1].resolve(response(record()));
  await listing;
  assert.equal(f.controller.state.tasks.length, 0);
  assert.equal(f.controller.state.total, 0);
  assert.deepEqual(plain(f.requests[2].params), { task_id: first });
  assert.deepEqual(f.requests.map((item) => item.method), ["task_history", "task_history", "task_forget"]);
});

test("disconnect during forget cannot mutate the next generation and disposal stops notifications", async () => {
  const f = fixture();
  await load(f, record());
  const forgetting = f.controller.forget(first);
  f.controller.reset();
  await load(f, record(second));
  f.requests[1].resolve({ forgotten: true });
  assert.equal(await forgetting, false);
  assert.equal(f.controller.state.tasks[0].task_id, second);
  const listing = f.controller.refresh();
  const count = f.updates.length;
  f.controller.dispose();
  f.requests.at(-1).reject(new Error("late"));
  await listing;
  assert.equal(f.updates.length, count);
});
