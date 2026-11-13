import test from "node:test";
import assert from "node:assert/strict";
import { regionalPointDetails, regionalReportDetails, regionalSpacing, regionalSpacingLabel } from "./regional-display.ts";

const detail = (rows, label) => rows.find((row) => row.label === label)?.value;

test("regional diagnostics preserve genuine zero and signed AOD adjustments", () => {
  const rows = regionalPointDetails({
    quality_index: 0, dominant_burnable_pressure_family: null, burnable_cloud_coverage_effective: 0,
    burnable_duration_minutes: 0, quality_aod550: 0, incoming_aod_adjustment: .125,
    observer_aod_adjustment: -.025, confidence_score: 0,
  }, true);
  assert.deepEqual(rows.map((row) => row.value), ["0.000", "无可烧目标云", "0.0%", "0 分钟", "0.000", "+0.125", "-0.025", "0%"]);
  assert.equal(detail(regionalPointDetails({ incoming_aod_adjustment: 0 }, true), "入射修正"), "+0.000");
});

test("failed/missing regional cells never display stale diagnostics or invented no-cloud state", () => {
  assert.ok(regionalPointDetails({ quality_index: 2, dominant_burnable_pressure_family: null }, false).every((row) => row.value === "--"));
  assert.ok(regionalPointDetails({ quality_index: "0", confidence_score: NaN }, true).every((row) => row.value === "--"));
  assert.equal(detail(regionalPointDetails({ dominant_burnable_pressure_family: "middle" }, true), "主导可烧压力族"), "中云压力族");
});

test("native CAMS receipt reveals consumed valid times without fabricating a run", () => {
  const rows = regionalReportDetails({
    cycle_time_utc: "2026-11-11T00:00:00Z",
    input_manifest: { sources: [{ role: "model:0" }, { role: "cams:2026-11-11T09:00:00Z" }, { role: "cams:2026-11-11T09:00:00Z" }] },
    precision: { point_spacing_deg: .25, model_grid_spacing_deg: .25, vertical_profiles: true, full_point_coverage: false },
  });
  assert.equal(detail(rows, "CAMS 起报 UTC"), "未提供");
  assert.equal(detail(rows, "CAMS 有效时次 UTC"), "2026-11-11 09:00:00Z");
  assert.equal(detail(rows, "垂直剖面"), "是");
  assert.equal(detail(rows, "完整点覆盖"), "否");
  assert.equal(detail(rows, "采样网格"), "0.25° × 0.25°");
});

test("Web provenance and explicit precision win over receipt and preserve missing metadata", () => {
  const report = { data_provenance: { cams_run_times_utc: ["2026-11-10T12:00:00Z"], cams_valid_times_utc: ["2026-11-11T12:00:00Z"], gfs_forecast_hours: [0, 3] }, precision: { point_spacing_deg: .5 } };
  assert.equal(detail(regionalReportDetails(report), "CAMS 起报 UTC"), "2026-11-10 12:00:00Z");
  assert.equal(detail(regionalReportDetails(report), "模式预报时效"), "0h · 3h");
  assert.equal(regionalSpacing(report), .5);
  assert.equal(regionalSpacingLabel(null), "网格精度未提供");
  assert.equal(regionalSpacing({ precision: { point_spacing_deg: 0 } }), null);
});
