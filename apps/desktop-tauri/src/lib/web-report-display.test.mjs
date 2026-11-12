import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

function load(name, dependencies = {}) {
  const exports = {};
  const source = ts.transpileModule(readFileSync(new URL(name, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(source, { exports, Date, Intl, Set, require: (id) => dependencies[id] });
  return exports;
}
const display = load("./forecast-display.ts");
const api = load("./web-report-display.ts", { "./forecast-display": display });
const plain = (value) => JSON.parse(JSON.stringify(value));

test("web times respect explicit timezone while naive local time remains literal", () => {
  assert.equal(api.displayTime("2026-11-11T10:30:00Z", "Asia/Shanghai"), "18:30");
  assert.equal(api.displayTime("2026-11-11T18:30:00+08:00", "America/Los_Angeles"), "02:30");
  assert.equal(api.displayTime("2026-11-11T18:30:00", "America/Los_Angeles", true), "2026-11-11 18:30");
  assert.equal(api.displayTime("invalid", "Asia/Shanghai"), "--");
  assert.equal(api.displayTime("2026-11-11T10:30:00Z", "invalid-zone"), "--");
});

test("summaries and measurements retain real zero without coercing missing values", () => {
  assert.deepEqual(plain(api.summaryLines({ summary: ["first", "", null, "second"] })), ["first", "second"]);
  assert.deepEqual(plain(api.summaryLines({ summary: "single" })), ["single"]);
  assert.equal(api.range(0, 1000, "km", 1000), "0.0 - 1.0 km");
  assert.equal(api.range(null, 1000, "km"), "--");
  assert.equal(api.signed(0), "0.000");
  assert.equal(api.signed(.125), "+0.125");
  assert.equal(api.signed(-.25), "-0.250");
  assert.equal(api.signed(null), "--");
});

test("quality sections preserve the original Web detail units and unavailable values", () => {
  const sections = api.qualitySections({ quality_index: 0, aerosol: { aod550: 0 }, quality_components: {
    forward_burnable_cloud_field_amount_peak: 0, duration_adjustment: -.25,
    incoming_aod_illumination_factor: 0, observer_return_transmission_mean: .5,
  } });
  const rows = new Map(sections.flatMap((section) => section.rows).map(({ label, value }) => [label, value]));
  assert.equal(rows.get("前方可烧云量"), "0%");
  assert.equal(rows.get("持续时间修正"), "-0.250");
  assert.equal(rows.get("太阳入射因子"), "×0.000");
  assert.equal(rows.get("观测回程透过率 T"), "50%");
  assert.equal(rows.get("最终质量分 / 2.5"), "0.000");
  assert.equal(rows.get("本地 AOD 550"), "0.000");
  assert.equal(rows.get("前方总云场量"), "--");
});

test("nowcast diagnostic units and context-specific clear labels are not conflated", () => {
  const rows = new Map(api.nowcastSections({ nowcast: {
    satellite_low_cloud_state: "clear", convective_score_ceiling: .25, radar_coverage_ratio: 0, satellite_retrieval_quality: "high",
  } }).flatMap((section) => section.rows).map(({ label, value }) => [label, value]));
  assert.equal(rows.get("低云状态"), "未检出低云");
  assert.equal(rows.get("对流评分上限"), "25%");
  assert.equal(rows.get("雷达覆盖"), "0%");
  assert.equal(rows.get("雷达来源"), "--");
  assert.equal(rows.get("卫星反演质量"), "高");
});

test("report links include both model reports and deduplicate shared paths", () => {
  assert.deepEqual(plain(api.reportPaths({ html_report_path: "/a.html", model_results: {
    gfs: { html_report_path: "/a.html" }, ec: { html_report_path: "/b.html" }, bad: { html_report_path: null },
  } })), [
    { label: "完整诊断报告", path: "/a.html" }, { label: "EC 诊断报告", path: "/b.html" },
  ]);
  assert.deepEqual(plain(api.reportPaths(null)), []);
});

test("browser report actions only use content-addressed same-service report URLs", () => {
  const url = `/api/v1/firesky/reports/${"a".repeat(64)}`;
  const result = { html_report_path: "/server/private/report.html", html_report_url: url, model_results: {
    external: { html_report_url: "https://example.com/report.html" },
    traversal: { html_report_url: "/api/v1/firesky/reports/../../credentials" },
    duplicate: { html_report_url: url },
  } };
  assert.deepEqual(plain(api.reportPaths(result, true)), [{ label: "完整诊断报告", path: url }]);
  assert.deepEqual(plain(api.reportPaths({ html_report_path: "/server/private/report.html" }, true)), []);
});
