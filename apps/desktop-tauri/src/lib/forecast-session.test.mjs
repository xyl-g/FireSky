import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("./forecast-session.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const exports = {};
vm.runInNewContext(source, { exports, structuredClone, Error }, { filename: "forecast-session.ts" });
const { ForecastSessionController } = exports;
const plain = (value) => JSON.parse(JSON.stringify(value));
const published = (f, kind = "point") => f.controller.state.published[kind] ?? null;
const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const response = (session = "session-1", ready = true) => ({
  session, report: { ready, datasets: [{
    source: "gfs", ready, missing_count: 0, verified: true, is_latest: true,
  }] },
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
    starts, requests, updates, transport,
    controller: new ForecastSessionController(transport, (state) => updates.push(state)),
  };
}
async function checked(f, ready = true) {
  const checking = f.controller.check("point", { lat: 0, lon: 0 });
  f.starts.at(-1).resolve(response("session-1", ready));
  await checking;
}

test("prediction check snapshots kind/payload and does not automatically prepare, publish or forecast", async () => {
  const f = fixture();
  const payload = { lat: 0, nested: { zero: 0 }, regions: ["china"] };
  const checking = f.controller.check("nowcast", payload);
  payload.nested.zero = 100;
  payload.regions[0] = "changed";
  assert.deepEqual(plain(f.starts[0].params), {
    kind: "nowcast", payload: { lat: 0, nested: { zero: 0 }, regions: ["china"] },
  });
  f.starts[0].resolve(response());
  await checking;
  assert.equal(f.controller.state.report.datasets[0].missing_count, 0);
  assert.equal(published(f), null);
  assert.deepEqual(f.starts.map((call) => call.method), ["check"]);
  assert.equal(f.requests.length, 0);
});

test("forecast requires a checked ready session and sends explicit confirmation exactly once", async () => {
  const f = fixture();
  await f.controller.forecast();
  assert.equal(f.starts.length, 0);
  await checked(f, false);
  await f.controller.forecast();
  assert.equal(f.starts.length, 1);
  const preparing = f.controller.prepare();
  f.starts[1].resolve(response());
  await preparing;
  const forecasting = f.controller.forecast();
  assert.equal(f.controller.state.phase, "forecasting");
  assert.equal(f.controller.state.session, null);
  assert.deepEqual(plain(f.starts[2].params), { session: "session-1", confirmed: true });
  await f.controller.forecast();
  assert.equal(f.starts.length, 3);
  f.starts[2].resolve({ model_results: { gfs: { peak_quality_index: 0 } }, model_errors: {} });
  await forecasting;
  assert.equal(published(f).result.model_results.gfs.peak_quality_index, 0);
  assert.equal(f.controller.state.phase, "idle");
  await f.controller.forecast();
  assert.equal(f.starts.length, 3);
});

test("complete older or unverified cycles require a separate acknowledgement", async () => {
  for (const freshness of [
    { verified: true, is_latest: false, latest_available_cycle: "2026-11-12 00Z" },
    { verified: false, is_latest: false, latest_available_cycle: "未核验" },
  ]) {
    const f = fixture();
    const checking = f.controller.check("regional", { regions: ["china"] });
    f.starts[0].resolve({
      session: "session-1",
      report: {
        ready: true,
        datasets: [{
          source: "gfs", ready: true, request_cycle: "2026-11-11 00Z",
          missing_count: 0, missing_hours: [], ...freshness,
        }],
      },
    });
    await checking;
    await f.controller.forecast();
    assert.equal(f.starts.length, 1);
    assert.match(f.controller.state.error, /确认/);
    assert.equal(f.controller.state.session, "session-1");

    const forecasting = f.controller.forecast(true);
    assert.deepEqual(plain(f.starts[1].params), {
      session: "session-1", confirmed: true, cycle_age_confirmed: true,
    });
    f.starts[1].resolve({ model_results: { gfs: { peak_quality_index: 0 } } });
    await forecasting;
    assert.equal(published(f, "regional").result.model_results.gfs.peak_quality_index, 0);
  }
});

test("published results survive draft checks and invalidation and remain isolated by kind", async () => {
  const f = fixture();
  await checked(f);
  const forecasting = f.controller.forecast();
  f.starts[1].resolve({ quality_index: 0 });
  await forecasting;
  await f.controller.publications();
  await f.controller.prepare();
  const point = published(f);
  assert.equal(point.result.quality_index, 0);
  const checking = f.controller.check("regional", { regions: ["china"] });
  assert.equal(published(f), point);
  assert.equal(published(f, "regional"), null);
  f.starts[2].resolve(response("second"));
  await checking;
  const next = f.controller.forecast();
  f.starts[3].resolve({ quality_index: 1 });
  await next;
  f.controller.invalidate();
  assert.equal(published(f), point);
  assert.equal(published(f, "regional").result.quality_index, 1);
});

test("failed forecast consumes the token and requires a new check", async () => {
  const f = fixture();
  await checked(f);
  const forecasting = f.controller.forecast();
  f.starts[1].reject(new Error("integrity check failed"));
  await forecasting;
  assert.equal(f.controller.state.phase, "error");
  assert.equal(f.controller.state.session, null);
  assert.equal(f.controller.state.report, null);
  assert.equal(f.controller.state.requestSnapshot, null);
  assert.equal(f.controller.state.error, "integrity check failed");
  assert.deepEqual(plain(f.requests[0].params), { session: "session-1" });
  await f.controller.prepare();
  await f.controller.forecast();
  assert.equal(f.starts.length, 2);
});

test("failed preparation revokes readiness and cannot start a forecast", async () => {
  const f = fixture();
  await checked(f);
  const preparing = f.controller.prepare();
  assert.equal(f.controller.state.report.ready, false);
  f.starts[1].reject(new Error("download failed"));
  await preparing;
  await f.controller.forecast();
  assert.equal(f.controller.state.report, null);
  assert.deepEqual(f.starts.map((call) => call.method), ["check", "prepare"]);
});

test("invalidation dismisses a late check token and blocks until the cancelled request terminates", async () => {
  const f = fixture();
  const checking = f.controller.check("point", {});
  f.controller.invalidate();
  await f.controller.check("regional", {});
  assert.equal(f.starts.length, 1);
  assert.equal(f.controller.state.phase, "cancelling");
  f.starts[0].cancellation.resolve({ requested: true });
  await flush();
  assert.equal(f.controller.state.phase, "cancelling");
  f.starts[0].resolve(response("late-token"));
  await checking;
  assert.equal(f.controller.state.phase, "idle");
  assert.equal(f.controller.state.session, null);
  assert.deepEqual(plain(f.requests[0].params), { session: "late-token" });
});

test("forecast cancellation dismisses its already-consumed UI token and ignores late results", async () => {
  const f = fixture();
  await checked(f);
  const forecasting = f.controller.forecast();
  let stopped = false;
  const cancelling = f.controller.cancel().then(() => { stopped = true; });
  assert.deepEqual(plain(f.requests[0].params), { session: "session-1" });
  f.starts[1].cancellation.resolve({ requested: true });
  await flush();
  assert.equal(stopped, false);
  f.starts[1].resolve({ quality_index: 2 });
  await Promise.all([forecasting, cancelling]);
  assert.equal(stopped, true);
  assert.equal(f.controller.state.phase, "idle");
  assert.equal(published(f), null);
});

test("publication calls deduplicate and late replies cannot overwrite preparation", async () => {
  const f = fixture();
  await checked(f);
  const publishing = f.controller.publications();
  const duplicate = f.controller.publications();
  assert.equal(f.requests.length, 1);
  const preparing = f.controller.prepare();
  assert.equal(f.controller.state.publicationPending, false);
  f.starts[1].resolve(response());
  await preparing;
  f.requests[0].resolve(response("session-1", false));
  await Promise.all([publishing, duplicate]);
  assert.equal(f.controller.state.report.ready, true);
});

test("late publications cannot overwrite an already-consumed forecast", async () => {
  const f = fixture();
  await checked(f);
  const publishing = f.controller.publications();
  const forecasting = f.controller.forecast();
  f.starts[1].resolve({ quality_index: 0 });
  await forecasting;
  f.requests[0].resolve(response());
  await publishing;
  assert.equal(f.controller.state.session, null);
  assert.equal(f.controller.state.report, null);
  assert.equal(published(f).result.quality_index, 0);
});

test("publication failure preserves local readiness and a fresh snapshot isolates old replies", async () => {
  const f = fixture();
  await checked(f);
  const first = f.controller.publications();
  f.requests[0].reject(new Error("publication unavailable"));
  await first;
  assert.equal(f.controller.state.report.ready, true);
  assert.equal(f.controller.state.publicationError, "publication unavailable");
  const old = f.controller.publications();
  const checking = f.controller.check("point", {});
  f.starts[1].resolve(response("new-session"));
  await checking;
  const latest = f.controller.publications();
  f.requests.at(-1).resolve(response("new-session", true));
  await latest;
  f.requests[1].resolve(response("session-1", false));
  await old;
  assert.equal(f.controller.state.session, "new-session");
  assert.equal(f.controller.state.report.ready, true);
});

test("progress is request-scoped, preserves zero and stops after cancellation", async () => {
  const f = fixture();
  const checking = f.controller.check("point", {});
  f.controller.progress("other", { percent: 99 });
  assert.equal(f.controller.state.progress, null);
  f.controller.progress("request-1", { percent: 0, stage: "check" });
  assert.equal(f.controller.state.progress.percent, 0);
  const cancelling = f.controller.cancel();
  f.controller.progress("request-1", { percent: 100 });
  assert.equal(f.controller.state.progress.percent, 0);
  f.starts[0].cancellation.resolve({ requested: false });
  f.starts[0].reject(new Error("cancelled"));
  await Promise.all([checking, cancelling]);
  assert.equal(f.controller.state.error, "");
});

test("parameter changes during preparation discard late readiness and dismiss the token", async () => {
  const f = fixture();
  await checked(f);
  const preparing = f.controller.prepare();
  f.controller.invalidate();
  f.starts[1].cancellation.resolve({ requested: false });
  f.starts[1].resolve(response());
  await preparing;
  assert.equal(f.controller.state.phase, "idle");
  assert.equal(f.controller.state.session, null);
  assert.equal(f.controller.state.report, null);
  assert.equal(f.requests[0].method, "dismiss");
});

test("dispose cancels a forecast and never emits further updates", async () => {
  const f = fixture();
  await checked(f);
  const forecasting = f.controller.forecast();
  const count = f.updates.length;
  f.controller.dispose();
  assert.equal(f.starts[1].cancels, 1);
  f.starts[1].cancellation.reject(new Error("transport exited"));
  f.starts[1].resolve({ quality_index: 2 });
  await forecasting;
  await f.controller.check("point", {});
  await f.controller.forecast();
  f.controller.invalidate();
  assert.equal(f.updates.length, count);
  assert.equal(f.requests[0].method, "dismiss");
});

test("dispose still dismisses a late check token without notifying", async () => {
  const f = fixture();
  const checking = f.controller.check("point", {});
  f.controller.dispose();
  const count = f.updates.length;
  f.starts[0].cancellation.resolve({ requested: true });
  f.starts[0].resolve(response("late-after-dispose"));
  await checking;
  assert.equal(f.updates.length, count);
  assert.deepEqual(plain(f.requests[0].params), { session: "late-after-dispose" });
});

test("synchronous forecast start failures invalidate and dismiss the single-use token", async () => {
  const f = fixture();
  await checked(f);
  f.transport.start = () => { throw new Error("start unavailable"); };
  await f.controller.forecast();
  assert.equal(f.controller.state.phase, "error");
  assert.equal(f.controller.state.session, null);
  assert.deepEqual(plain(f.requests[0].params), { session: "session-1" });
});

async function publish(f, kind = "point", payload = { name: "original", timezone_name: "Asia/Shanghai" }, result = { quality_index: 0 }) {
  const checking = f.controller.check(kind, payload, `${kind} label`);
  f.starts.at(-1).resolve(response());
  await checking;
  const forecasting = f.controller.forecast();
  f.starts.at(-1).resolve(result);
  await forecasting;
  return published(f, kind);
}

test("published identity is the deep immutable check snapshot, not input or transport-owned params", async () => {
  const f = fixture();
  const payload = { name: "杭州", lat: 0, timezone_name: "Asia/Shanghai", nested: { rows: [0, null] } };
  const checking = f.controller.check("point", payload, "original label");
  payload.name = "changed input";
  payload.nested.rows[0] = 9;
  f.starts[0].params.payload.name = "changed transport";
  f.starts[0].params.payload.nested.rows[0] = 5;
  f.starts[0].resolve(response());
  await checking;
  const before = Date.now();
  const forecasting = f.controller.forecast();
  const updatesBefore = f.updates.length;
  f.starts[1].resolve({ quality_index: 0, html_report_path: "/fixture/original.html" });
  await forecasting;
  const result = published(f);
  assert.equal(result.kind, "point");
  assert.equal(result.requestId, "request-2");
  assert.ok(result.publishedAtUtc.endsWith("Z"));
  assert.ok(Date.parse(result.publishedAtUtc) >= before);
  assert.equal(result.requestSnapshot.label, "original label");
  assert.deepEqual(plain(result.requestSnapshot.payload), { name: "杭州", lat: 0, timezone_name: "Asia/Shanghai", nested: { rows: [0, null] } });
  assert.equal(Object.isFrozen(result.requestSnapshot.payload.nested.rows), true);
  assert.equal(f.controller.state.requestSnapshot, null);
  assert.equal(f.updates.length, updatesBefore + 1);
  assert.equal(f.updates.at(-1).published.point, result);
});

for (const method of ["check", "prepare", "forecast"]) {
  for (const synchronous of [false, true]) {
    test(`a second ${method} ${synchronous ? "start" : "worker"} failure preserves the full published result`, async () => {
      const f = fixture();
      const first = await publish(f);
      if (method !== "check") await checked(f);
      const start = f.transport.start;
      if (synchronous) f.transport.start = () => { throw new Error("fixture unavailable"); };
      const running = method === "check" ? f.controller.check("point", { name: "replacement" }) : f.controller[method]();
      assert.equal(published(f), first);
      if (!synchronous) f.starts.at(-1).reject(new Error("fixture failed"));
      await running;
      assert.equal(published(f), first);
      assert.equal(f.controller.state.requestSnapshot, null);
      assert.equal(f.controller.state.session, null);
      f.transport.start = start;
    });
  }
}

for (const action of ["cancel", "invalidate", "dispose"]) {
  test(`second forecast ${action} ignores late success and retains the prior publication`, async () => {
    const f = fixture();
    const first = await publish(f);
    await checked(f);
    const running = f.controller.forecast();
    const late = f.starts.at(-1);
    const cancelling = f.controller[action]();
    late.cancellation.resolve({ requested: true });
    late.resolve({ quality_index: 2.5, html_report_path: "/fixture/stale.html" });
    await Promise.all([running, cancelling]);
    assert.equal(published(f), first);
  });
}

test("success replaces result, identity and report atomically for only its own kind", async () => {
  const f = fixture();
  const point = await publish(f);
  const nowcast = await publish(f, "nowcast", { name: "satellite point" }, { quality_index: .4 });
  const regional = await publish(f, "regional", { regions: ["china"], custom_bounds: { west: 0 } }, { points: [] });
  const replacement = await publish(f, "point", { name: "new point", timezone_name: "America/New_York" }, { quality_index: 1.5, html_report_path: "/fixture/new.html" });
  assert.notEqual(replacement, point);
  assert.equal(replacement.requestSnapshot.payload.name, "new point");
  assert.equal(replacement.requestSnapshot.payload.timezone_name, "America/New_York");
  assert.equal(replacement.result.html_report_path, "/fixture/new.html");
  assert.equal(replacement.requestId, "request-8");
  assert.equal(published(f, "nowcast"), nowcast);
  assert.equal(published(f, "regional"), regional);
  f.controller.invalidate();
  assert.equal(published(f), replacement);
});

for (const malformed of [null, [], "unexpected result"]) {
  test(`a malformed successful forecast response (${JSON.stringify(malformed)}) cannot replace published data`, async () => {
    const f = fixture();
    const first = await publish(f);
    await checked(f);
    const running = f.controller.forecast();
    f.starts.at(-1).resolve(malformed);
    await running;
    assert.equal(published(f), first);
    assert.equal(f.controller.state.phase, "error");
    assert.equal(f.controller.state.requestSnapshot, null);
  });
}
