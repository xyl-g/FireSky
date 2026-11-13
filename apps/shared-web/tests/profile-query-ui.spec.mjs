import { test, expect } from "@playwright/test";

const A = "2026-11-11T10:00:00.123456Z";
const B = "2026-11-11T10:05:00.123457Z";
const C = "2026-11-11T10:10:00.123458Z";
function resultFixture(satellite) {
  const section = (value, utc = A) => ({
    model_source: "gfs", solar_azimuth_deg: 270, valid_time_local: utc,
    distances_km: [0, 20], pressure_levels_hpa: [850, 500],
    terrain_elevation_asl_m: [0, 0], required_illumination_base_asl_m: [1000, 2000],
    fields: { geopotential_height_asl_m: [[1000, 5000], [1000, 5000]], cloud_fraction_percent: [[value, 0], [20, null]] },
  });
  const source = (model) => ({
    contract: "rust-desktop-profile-source-v1", available: true,
    result_id: (model === "ec" ? "e" : "a").repeat(64), source_identity: { bytes: 0, sha256: "b".repeat(64) },
    model_source: model, cycle_utc: "2026-11-11T00:00:00Z", query_method: "profile_query",
    profile_kind: model === "satellite" ? "satellite-step-solar-path-diagnostic" : "model-pressure-cross-section",
  });
  const report = (model) => ({
    peak_quality_index: 1, best_time_utc: A, best_time_local: A, cross_section: section(10), profile_source: source(model),
    steps: [A, B, C].map((utc, index) => ({ valid: true, valid_time_utc: utc, valid_time_local: utc, instant_quality_index: index === 0 ? 0 : 1 })),
    nowcast: { satellite_vertical_profile: {
      status: "partial_unknown", valid_time_utc: A, coverage: 1, bands: ["B07", "B13", "B15"],
      samples: [0, 20].map((distance) => ({ distance_km: distance, bearing_deg: 270, status: "retrieved",
        levels: [{ height_m: 1000, pressure_hpa: 850, status: "retrieved", hsd_posterior_percent: 0 }, { height_m: 5000, pressure_hpa: 500, status: "unknown", hsd_posterior_percent: null }] })),
    } },
  });
  return { result: satellite ? report("satellite") : { model_results: { gfs: report("gfs"), ec: report("ec") } }, section };
}
async function fixture(page, mode, { satellite = false, delayed = false } = {}) {
  const { result, section } = resultFixture(satellite);
  const calls = [];
  const response = (method, params) => {
    if (method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [] };
    if (method === "check") return { session: "profile-query-fixture", report: { ready: true, datasets: [] } };
    if (method === "forecast") return result;
    if (method === "dismiss") return { dismissed: true };
    if (method === "profile_query") return {
      ...params, contract: "rust-desktop-profile-query-v1", cycle_utc: "2026-11-11T00:00:00Z",
      status: params.valid_time_utc === C ? "expired" : "ready",
      profile_kind: satellite ? "satellite-step-solar-path-diagnostic" : "model-pressure-cross-section",
      cross_section: satellite ? {
        azimuth_deg: params.valid_time_utc === B ? 280 : 270, samples_count: 1,
        summary: [`光路 ${params.valid_time_utc}`], profile: [{ distance_km: 0, bearing_deg: params.valid_time_utc === B ? 280 : 270, cloud_base_m: 0, cloud_top_m: 5000, target_cloud_score: 0, blocker_risk: 0, illumination_status: "illuminated" }],
      } : section(params.model_source === "ec" ? 40 : params.valid_time_utc === B ? 70 : 0, params.valid_time_utc),
    };
    if (method === "task_history") return { schema: "firesky-task-history-v1", tasks: [], total: 0, issues: [], automatic_restart: false };
    throw new Error(`Unexpected fixture method ${method}`);
  };
  const jobs = new Map();
  await page.route("**/api/v1/firesky/desktop/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname.replace("/api/v1/firesky/desktop", "");
    const body = request.method() === "POST" ? request.postDataJSON() : null;
    if (path === "/status") return route.fulfill({ json: { running: true, instance_id: "profiles", protocol: 1, engine: "firesky-rust", workspace: "/fixture" } });
    if (path === "/jobs") {
      jobs.set(body.request_id, body); calls.push({ method: body.method, params: body.params });
      return route.fulfill({ json: { request_id: body.request_id, instance_id: "profiles", status: "running" } });
    }
    const [, , id, action] = path.split("/");
    if (action === "release") { jobs.delete(id); return route.fulfill({ json: { released: true } }); }
    if (action === "cancel") return route.fulfill({ json: { requested: true } });
    const job = jobs.get(id);
    if (delayed && job.method === "profile_query" && job.params.valid_time_utc === A) await new Promise((resolve) => setTimeout(resolve, 500));
    return route.fulfill({ json: { request_id: id, instance_id: "profiles", status: "done", progress_seq: 0, result: response(job.method, job.params) } });
  });
  await page.goto("/?mapOffline=1");
  if (satellite) await page.getByRole("button", { name: "实况观测", exact: true }).click();
  await page.getByRole("button", { name: satellite ? "开始实况分析" : "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: satellite ? "实况准备" : "预测准备", exact: true });
  await dialog.getByRole("button", { name: satellite ? "确认分析" : "确认预测", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator(".report-actions time")).toBeVisible();
  return calls;
}

for (const mode of ["http"]) {
  test(`${mode} shares precise UTC selection across trend, ledger and model profiles`, async ({ page }) => {
    const calls = await fixture(page, mode);
    await page.getByRole("slider", { name: "逐时时刻", exact: true }).press("ArrowRight");
    await page.getByRole("tab", { name: "逐时台账", exact: true }).click();
    const selected = page.locator(".ledger-scroll tr.selected");
    await expect(selected).toHaveCount(1); await expect(selected.locator("button")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByLabel("结果时次", { exact: true })).toHaveValue(B);
    await page.getByRole("tab", { name: "大气剖面", exact: true }).click();
    const chart = page.getByRole("img", { name: "沿太阳方向的模式云量剖面", exact: true });
    await expect(chart.locator(".profile-cell").first()).toHaveAccessibleName(/模式云量 70%/);
    expect(calls.filter((call) => call.method === "profile_query").at(-1).params.valid_time_utc).toBe(B);
    await page.getByRole("group", { name: "结果模式", exact: true }).getByRole("button", { name: "EC", exact: true }).click();
    await expect(page.getByLabel("结果时次", { exact: true })).toHaveValue(A);
    await expect(chart.locator(".profile-cell").first()).toHaveAccessibleName(/模式云量 40%/);
    await page.getByRole("group", { name: "结果模式", exact: true }).getByRole("button", { name: "GFS", exact: true }).click();
    await expect(page.getByLabel("结果时次", { exact: true })).toHaveValue(B);
    await expect(chart.locator(".profile-cell").first()).toHaveAccessibleName(/模式云量 70%/);
    await page.getByLabel("结果时次", { exact: true }).selectOption(C);
    await expect(page.getByText("该结果的剖面资源已过期，请显式重新预测。", { exact: true })).toBeVisible();
    await expect(chart).toHaveCount(0);
    expect(calls.filter((call) => ["check", "forecast", "prepare"].includes(call.method)).map((call) => call.method)).toEqual(["check", "forecast"]);
  });

  test(`${mode} rejects a late old-time matrix after a rapid selection`, async ({ page }) => {
    await fixture(page, mode, { delayed: true });
    await page.getByRole("tab", { name: "大气剖面", exact: true }).click();
    await expect(page.getByText("正在读取所选时次剖面…", { exact: true })).toBeVisible();
    await page.getByLabel("结果时次", { exact: true }).selectOption(B);
    const cell = page.getByRole("img", { name: "沿太阳方向的模式云量剖面", exact: true }).locator(".profile-cell").first();
    await expect(cell).toHaveAccessibleName(/模式云量 70%/);
    await page.waitForTimeout(650);
    await expect(cell).toHaveAccessibleName(/模式云量 70%/);
  });

  test(`${mode} dynamic satellite paths never replace the true observation posterior`, async ({ page }, testInfo) => {
    await fixture(page, mode, { satellite: true });
    await page.getByRole("tab", { name: "大气剖面", exact: true }).click();
    const original = page.getByRole("img", { name: "沿太阳方向的卫星云层后验概率剖面", exact: true });
    await expect(original.locator(".profile-cell").first()).toHaveAccessibleName(/后验概率 0%/);
    await page.getByLabel("结果时次", { exact: true }).selectOption(B);
    const path = page.getByRole("region", { name: "所选时次太阳光路诊断", exact: true });
    await expect(path).toContainText(B); await expect(path).toContainText("280");
    await expect(original.locator(".profile-cell").first()).toHaveAccessibleName(/后验概率 0%/);
    await expect(page.locator(".profile .source-note")).toContainText("不发布 COT");
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 820 });
      await path.scrollIntoViewIfNeeded();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`dynamic-profile-${width}.png`), fullPage: true });
    }
  });
}
