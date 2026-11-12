import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("./data-session.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const exports = {};
vm.runInNewContext(source, { exports, structuredClone, Error }, { filename: "data-session.ts" });
const { DataSessionController } = exports;
const plain = (value) => JSON.parse(JSON.stringify(value));
const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const response = (session = "session-1", ready = true) => ({
  session, report: { ready, datasets: [{ source: "gfs", ready, missing_count: 0 }] },
});
function fixture() {
  const starts = [], requests = [], updates = [];
  const transport = {
    start(method, params) {
      const completion = deferred();
      const cancellation = deferred();
      const call = { method, params, ...completion, cancellation, cancels: 0 };
      starts.push(call);
      return {
        requestId: `request-${starts.length}`, result: completion.promise,
        cancel: () => { call.cancels += 1; return cancellation.promise; },
      };
    },
    request(method, params) {
      const completion = deferred();
      requests.push({ method, params, ...completion });
      if (method === "dismiss") completion.resolve({ dismissed: true });
      return completion.promise;
    },
  };
  return {
    starts, requests, updates,
    controller: new DataSessionController(transport, (state) => updates.push(state)),
  };
}

test("check snapshots payload, retains zero, and never automatically probes or forecasts", async () => {
  const { controller, starts, requests } = fixture();
  const payload = { regions: ["china"], nested: { zero: 0 } };
  const checking = controller.check(payload);
  payload.regions[0] = "changed";
  payload.nested.zero = 100;
  assert.deepEqual(plain(starts[0].params), {
    kind: "regional", payload: { regions: ["china"], nested: { zero: 0 } },
  });
  starts[0].resolve(response());
  await checking;
  assert.equal(controller.state.phase, "checked");
  assert.equal(controller.state.report.datasets[0].missing_count, 0);
  assert.equal(requests.length, 0);
  assert.deepEqual(starts.map((call) => call.method), ["check"]);
});

test("invalidation dismisses a late check token and blocks new work until terminal result", async () => {
  const { controller, starts, requests } = fixture();
  const checking = controller.check({ regions: ["china"] });
  controller.invalidate();
  assert.equal(controller.state.phase, "cancelling");
  assert.equal(starts[0].cancels, 1);
  await controller.check({ regions: ["europe"] });
  assert.equal(starts.length, 1);
  starts[0].cancellation.resolve({ requested: true });
  await flush();
  assert.equal(controller.state.phase, "cancelling");
  starts[0].resolve(response("stale-token"));
  await checking;
  assert.equal(controller.state.phase, "idle");
  assert.equal(controller.state.session, null);
  assert.deepEqual(plain(requests[0].params), { session: "stale-token" });
});

test("immediate cancellation waits for terminal result even after cancel acknowledgement", async () => {
  const { controller, starts } = fixture();
  const checking = controller.check({});
  let completed = false;
  const cancelling = controller.cancel().then(() => { completed = true; });
  starts[0].cancellation.resolve({ requested: true });
  await flush();
  assert.equal(completed, false);
  assert.equal(controller.state.phase, "cancelling");
  starts[0].reject(new Error("cancelled"));
  await Promise.all([checking, cancelling]);
  assert.equal(completed, true);
  assert.equal(controller.state.phase, "idle");
  assert.equal(controller.state.error, "");
});

test("failed preparation cannot preserve prior readiness or run a forecast", async () => {
  const { controller, starts } = fixture();
  const checking = controller.check({});
  starts[0].resolve(response());
  await checking;
  const preparing = controller.prepare();
  assert.equal(controller.state.report.ready, false);
  assert.deepEqual(plain(starts[1].params), { session: "session-1" });
  starts[1].reject(new Error("download failure"));
  await preparing;
  assert.equal(controller.state.phase, "error");
  assert.equal(controller.state.report.ready, false);
  assert.equal(controller.state.error, "download failure");
  assert.deepEqual(starts.map((call) => call.method), ["check", "prepare"]);
});

test("publication requests deduplicate and late replies cannot replace prepared report", async () => {
  const { controller, starts, requests } = fixture();
  const checking = controller.check({});
  starts[0].resolve(response());
  await checking;
  const publishing = controller.publications();
  const duplicate = controller.publications();
  assert.equal(requests.length, 1);
  assert.equal(controller.state.publicationPending, true);
  const preparing = controller.prepare();
  assert.equal(controller.state.publicationPending, false);
  starts[1].resolve(response("session-1", true));
  await preparing;
  requests[0].resolve(response("session-1", false));
  await Promise.all([publishing, duplicate]);
  assert.equal(controller.state.report.ready, true);
  assert.equal(controller.state.phase, "checked");
});

test("publication failure keeps local readiness and invalidate dismisses current token", async () => {
  const { controller, starts, requests } = fixture();
  const checking = controller.check({});
  starts[0].resolve(response());
  await checking;
  const publishing = controller.publications();
  requests[0].reject(new Error("upstream unavailable"));
  await publishing;
  assert.equal(controller.state.report.ready, true);
  assert.equal(controller.state.publicationError, "upstream unavailable");
  controller.invalidate();
  assert.equal(controller.state.report, null);
  assert.equal(requests[1].method, "dismiss");
});

test("a new snapshot can verify publications while an old upstream request is still pending", async () => {
  const { controller, starts, requests } = fixture();
  const checking = controller.check({});
  starts[0].resolve(response("old"));
  await checking;
  const oldPublishing = controller.publications();
  controller.invalidate();
  const checkingAgain = controller.check({});
  starts[1].resolve(response("new"));
  await checkingAgain;
  const newPublishing = controller.publications();
  assert.deepEqual(requests.map((call) => call.method), ["publications", "dismiss", "publications"]);
  requests[2].resolve(response("new", true));
  await newPublishing;
  requests[0].resolve(response("old", false));
  await oldPublishing;
  assert.equal(controller.state.session, "new");
  assert.equal(controller.state.report.ready, true);
});

test("parameter changes during preparation dismiss the token and ignore late readiness", async () => {
  const { controller, starts, requests } = fixture();
  const checking = controller.check({});
  starts[0].resolve(response());
  await checking;
  const preparing = controller.prepare();
  controller.invalidate();
  starts[1].cancellation.resolve({ requested: false });
  starts[1].resolve(response());
  await preparing;
  assert.equal(requests[0].method, "dismiss");
  assert.equal(controller.state.phase, "idle");
  assert.equal(controller.state.report, null);
  assert.equal(controller.state.session, null);
});

test("progress is scoped to current request and stops after cancellation", async () => {
  const { controller, starts } = fixture();
  const checking = controller.check({});
  controller.progress("other-request", { percent: 99 });
  assert.equal(controller.state.progress, null);
  controller.progress("request-1", { percent: 0, stage: "check" });
  assert.equal(controller.state.progress.percent, 0);
  const cancelling = controller.cancel();
  controller.progress("request-1", { percent: 100 });
  assert.equal(controller.state.progress.percent, 0);
  starts[0].cancellation.resolve({ requested: false });
  starts[0].reject(new Error("cancelled"));
  await Promise.all([checking, cancelling]);
});

test("dispose cancels active work, dismisses late tokens, and never notifies again", async () => {
  const { controller, starts, requests, updates } = fixture();
  const checking = controller.check({});
  const count = updates.length;
  controller.dispose();
  assert.equal(starts[0].cancels, 1);
  starts[0].cancellation.reject(new Error("already exited"));
  starts[0].resolve(response("late-after-dispose"));
  await checking;
  await controller.check({});
  await controller.prepare();
  await controller.publications();
  controller.invalidate();
  assert.equal(updates.length, count);
  assert.equal(starts.length, 1);
  assert.deepEqual(plain(requests[0].params), { session: "late-after-dispose" });
});
