import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";
const load = (name, dependencies = {}) => {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports, Date, Set, Error, require: (name) => dependencies[name] });
  return exports;
};
const { ProfileQueryController, profileRequest, profileRequestAt, profileResponseMatches } = load("profile-query", { "./forecast-time": load("forecast-time") });
const source = { contract: "rust-desktop-profile-source-v1", available: true, query_method: "profile_query", model_source: "gfs",
  profile_kind: "model-pressure-cross-section", cycle_utc: "2026-11-11T00:00:00Z", result_id: "a".repeat(64), source_identity: { bytes: 0, sha256: "b".repeat(64) } };
const published = { requestSnapshot: { payload: { lat: 0, lon: 0 } } };
const request = () => profileRequest(published, { profile_source: source }, "gfs", "2026-11-11T18:00:00.123456+08:00");
const reply = (input, patch = {}) => ({ ...input, contract: "rust-desktop-profile-query-v1", cycle_utc: source.cycle_utc, status: "ready", profile_kind: source.profile_kind, cross_section: { zero: 0 }, ...patch });
function fixture() {
  const calls = [];
  const controller = new ProfileQueryController((method, params) => {
    let resolve, reject;
    const call = { method, params, cancels: 0, result: new Promise((yes, no) => { resolve = yes; reject = no; }) };
    call.resolve = resolve; call.reject = reject; calls.push(call);
    return { requestId: `request-${calls.length}`, result: call.result, cancel: async () => { ++call.cancels; } };
  }, () => {});
  return { controller, calls };
}
test("dynamic request binds exact immutable source, published coordinates and UTC precision", () => {
  const value = request();
  assert.equal(value.lat, 0); assert.equal(value.source_identity.bytes, 0);
  assert.equal(value.valid_time_utc, "2026-11-11T10:00:00.123456Z");
  assert.equal(Object.keys(value).length, 6);
  assert.equal(profileRequest(published, { profile_source: source }, "ec", value.valid_time_utc), null);
  assert.equal(profileRequest(published, { profile_source: { ...source, cycle_utc: "2026-11-11T00:00:00" } }, "gfs", value.valid_time_utc), null);
  assert.equal(profileRequest(published, {}, "gfs", value.valid_time_utc), null);
});
test("regional point request binds the selected coordinate to the same frozen source contract", () => {
  const value = profileRequestAt({ profile_source: source }, "gfs", 30.25, 120.25, "2026-11-11T18:00:00+08:00");
  assert.deepEqual(JSON.parse(JSON.stringify(value)), {
    result_id: "a".repeat(64), source_identity: { bytes: 0, sha256: "b".repeat(64) },
    model_source: "gfs", lat: 30.25, lon: 120.25, valid_time_utc: "2026-11-11T10:00:00Z",
  });
  assert.equal(profileRequestAt({ profile_source: source }, "gfs", 91, 120.25, value.valid_time_utc), null);
  assert.equal(profileRequestAt({ profile_source: source }, "ec", 30.25, 120.25, value.valid_time_utc), null);
});
test("response matching rejects every cross-result/model/location/time identity mismatch", () => {
  const value = request();
  assert.equal(profileResponseMatches(reply(value), value), true);
  for (const patch of [{ result_id: "c".repeat(64) }, { model_source: "ec" }, { lat: 1 }, { lon: 1 }, { valid_time_utc: "2026-11-11T10:00:00.123457Z" }, { source_identity: { bytes: 1, sha256: "b".repeat(64) } }]) assert.equal(profileResponseMatches(reply(value, patch), value), false);
});
test("rapid time switches cancel old requests and discard late completions", async () => {
  const { controller, calls } = fixture(), a = request(), b = { ...a, valid_time_utc: "2026-11-11T10:00:01Z" };
  const first = controller.select(a, "publication:1:gfs", source.cycle_utc);
  const second = controller.select(b, "publication:1:gfs", source.cycle_utc);
  assert.equal(calls[0].cancels, 1);
  calls[1].resolve(reply(b, { cross_section: { zero: 0, selection: "b" } })); await second;
  calls[0].resolve(reply(a, { cross_section: { selection: "a" } })); await first;
  assert.equal(controller.state.crossSection.selection, "b");
  assert.equal(controller.state.crossSection.zero, 0);
  assert.deepEqual(calls.map((call) => call.method), ["profile_query", "profile_query"]);
});
test("generation changes invalidate a same-time query and never reuse old profile data", async () => {
  const { controller, calls } = fixture(), input = request();
  const old = controller.select(input, "publication:1:gfs");
  const next = controller.select(input, "publication:2:gfs");
  calls[0].resolve(reply(input)); await old;
  assert.equal(controller.state.phase, "loading"); assert.equal(controller.state.crossSection, null);
  calls[1].resolve(reply(input)); await next;
  assert.equal(controller.state.phase, "ready");
});
for (const status of ["expired", "missing", "unavailable", "cancelled"]) test(`${status} clears previously displayed data without preparing or forecasting`, async () => {
  const { controller, calls } = fixture(), input = request();
  const first = controller.select(input, "first"); calls[0].resolve(reply(input)); await first;
  const next = controller.select(input, "second", source.cycle_utc); calls[1].resolve(reply(input, { status, cross_section: null, cycle_utc: undefined })); await next;
  assert.equal(controller.state.phase, status); assert.equal(controller.state.crossSection, null);
  assert.deepEqual(calls.map((call) => call.method), ["profile_query", "profile_query"]);
});
for (const patch of [{ cross_section: null }, { profile_kind: "satellite-step-solar-path-diagnostic" }, { cycle_utc: "2026-11-11T06:00:00Z" }]) test(`invalid ready result cannot become a matrix: ${JSON.stringify(patch)}`, async () => {
  const { controller, calls } = fixture(), input = request();
  const running = controller.select(input, "context", source.cycle_utc);
  calls[0].resolve(reply(input, patch)); await running;
  assert.equal(controller.state.phase, "error"); assert.equal(controller.state.crossSection, null);
});
