import { test, expect } from "@playwright/test";
import { IN_PAGE_RPC_MOCK } from "./helpers/in-page-rpc.mjs";

async function fixture(page, pendingForecast = false) {
  await page.addInitScript(IN_PAGE_RPC_MOCK);
  await page.addInitScript(({ pendingForecast }) => {
    localStorage.clear();
    const tickets = new Map(), calls = [];
    const report = {
      model_source: "gfs", phenomenon: "dusk", peak_quality_index: 0, confidence_score: 0,
      anchor_local: "2026-11-11T17:04:00+08:00", best_time_local: "2026-11-11T17:00:00+08:00",
      model_cycles: { gfs: "2026-11-11T00:00:00Z" }, selected_regime: "distant_target",
      color_diagnosis: { type: "orange_red", confidence: .8 }, dominant_burnable_pressure_family: "high",
      dominant_burnable_cloud_phase: { label: "ice", ice_fraction: 0 },
      cloud_genus_proxy: { label: "cirrus", confidence: .7, candidate_scores: { cirrus: .7, altostratus: .6 } },
      cloud_evolution: { trend: "strengthening", cloud_hole_closure_risk: 0, comparison_minutes: 20 },
      summary: ["fixture 物理与气象结论", "fixture 第二条结论"],
      timing_window: { start_time_local: "2026-11-11T16:55:00+08:00", end_time_local: "2026-11-11T17:05:00+08:00" },
      boundary_uncertainty: {
        lower_distance_km: 0, upper_distance_km: 50, width_km: 50, classification_stable: false,
        nearest_flip_displacement_km: 0, start_time_earliest_local: "2026-11-11T16:55:00+08:00",
        start_time_latest_local: "2026-11-11T17:05:00+08:00", duration_min_minutes: 0, duration_max_minutes: 10,
      },
      quality_components: {
        physically_burnable_steps: 0, forward_burnable_cloud_field_amount_peak: 0, duration_adjustment: -.25,
        incoming_aod_adjustment: .125, observer_return_transmission_mean: .5, incoming_aod_illumination_factor: 0,
      },
      nowcast: { satellite_low_cloud_state: "clear", radar_coverage_ratio: 0, convective_score_ceiling: .25 },
      aerosol: { aod550: 0, source: "cams" },
      html_report_path: "/fixture/reports/web/native-html/" + "a".repeat(64) + ".html",
      html_report_url: "/api/v1/firesky/reports/" + "a".repeat(64),
      steps: [
        { valid: true, valid_time_local: "2026-11-11T17:00:00+08:00", instant_quality_index: 0, solar_elevation_deg: 0,
          dominant_burnable_pressure_family: "high", dominant_burnable_cloud_base_asl_mean_m: 0,
          dominant_burnable_cloud_top_asl_mean_m: 8000, burnable_cloud_coverage: 0, brightness_color: .5,
          forward_burnable_cloud_ratio: 0, observer_return_transmission: 1, solar_incoming_block_risk: 0, quality_aod550: 0 },
        { valid: false, valid_time_local: "2026-11-11T17:05:00+08:00", instant_quality_index: null,
          solar_elevation_deg: -1, burnable_cloud_coverage: .9 },
      ],
    };
    window.__desktopFixture = {
      calls,
      report,
      hasPending(method) { return [...tickets.values()].some((t) => t.method === method && !t.settled); },
      finishCancel() {
        for (const ticket of tickets.values()) {
          if (ticket.cancelled) ticket.reject({ code: "cancelled", message: "fixture cancelled" });
        }
      },
    };
    window.__rpcStatus = () => ({ generation: 1 });
    window.__rpcDirect = (method) => {
      calls.push({ command: "rpc_request", method, params: undefined });
      if (method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [] };
      if (method === "dismiss") return { dismissed: true };
      if (method === "cams_credentials") return { configured: false, can_save: true };
      if (method === "inventory") return { datasets: [{ source: "gfs", path: "/fixture/cache/gfs", total_file_count: 0, total_storage_mb: 0, cycles: {} }] };
      return undefined;
    };
    window.__rpcSubmit = (job) => {
      calls.push({ command: "rpc_start", method: job.method, params: job.params, requestId: job.id });
      tickets.set(job.id, job);
      if (job.method === "check") job.resolve({ session: "web-fixture", report: { ready: true, datasets: [] } });
      if (job.method === "forecast" && !pendingForecast) job.resolve({ model_results: { gfs: report, ec: { ...report, html_report_path: null } } });
    };
    window.__rpcCancelJob = (job) => {
      calls.push({ command: "rpc_cancel", requestId: job.id });
      job.cancelled = true;
      return true;
    };
  }, { pendingForecast });
  await page.goto("/?mapOffline=1");
  await expect(page.getByRole("button", { name: "开始预测", exact: true })).toBeEnabled();
}

async function startForecast(page) {
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await dialog.getByRole("button", { name: "确认预测", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}

test("Web details and complete hourly fields inhabit the existing four-tab UI", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    window.__openedReports = [];
    window.open = (path) => { window.__openedReports.push(path); return null; };
  });
  await fixture(page);
  await startForecast(page);
  await expect(page.getByRole("tab")).toHaveText(["概览", "大气剖面", "诊断与时间窗", "逐时台账"]);
  await expect(page.getByRole("region", { name: "时间与数据周期" })).toContainText("17:04");
  await expect(page.getByRole("region", { name: "云场与色彩诊断" })).toContainText("橙红");
  await expect(page.getByRole("region", { name: "云场与色彩诊断" })).toContainText("冰比例 0%");
  await expect(page.getByRole("region", { name: "物理与气象分析结论" }).getByRole("listitem")).toHaveCount(2);
  await page.getByRole("button", { name: "打开GFS 诊断报告", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__openedReports)).toHaveLength(1);
  await page.getByRole("tab", { name: "诊断与时间窗", exact: true }).click();
  const boundary = page.getByRole("region", { name: "目标云边界位置敏感度" });
  await expect(boundary).toContainText("0 - 50 km");
  await expect(boundary).toContainText("敏感");
  const scoring = page.getByRole("region", { name: "评分与修正" });
  await expect(scoring).toContainText("-0.250");
  await expect(scoring).toContainText("+0.125");
  await expect(page.getByRole("region", { name: "云体运动、雷达与约束" })).toContainText("未检出低云");
  for (const [width, height] of [[390, 667], [768, 720], [1024, 720], [1440, 720]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ["light", "dark"]) {
      await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
      await boundary.scrollIntoViewIfNeeded();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth &&
        document.querySelector(".workspace").scrollWidth <= document.querySelector(".workspace").clientWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`web-details-${width}-${theme}.png`), fullPage: true });
    }
  }
  await page.getByRole("tab", { name: "逐时台账", exact: true }).click();
  const ledger = page.getByRole("region", { name: "逐时质量明细" });
  await expect(ledger.getByRole("columnheader")).toHaveCount(13);
  const rows = ledger.locator("tbody tr");
  await expect(rows).toHaveCount(2);
  await expect(rows.first().locator("td").nth(6)).toHaveText("0.0 - 8.0 km ASL");
  await expect(rows.first().locator("td").nth(7)).toHaveText("0%");
  await expect(rows.first().locator("td").nth(10)).toHaveText("100%");
  await expect(rows.first().locator("td").nth(12)).toHaveText("0.000");
  await expect(rows.nth(1).locator("td").nth(7)).toHaveText("--");
  await rows.first().getByRole("button").click();
  await expect(rows.first().getByRole("button")).toHaveAttribute("aria-pressed", "true");
  expect(errors).toEqual([]);
});

test("workbench navigation and refresh shortcuts work without desktop-only actions", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "设置", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "单点预测", exact: true }).click();
  await page.keyboard.press("Control+k");
  await expect(page.getByLabel("搜索地名", { exact: true })).toBeFocused();
  await page.getByRole("button", { name: "区域预测", exact: true }).click();
  await expect(page.getByRole("heading", { name: "区域预测", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "数据与下载", exact: true }).click();
  // 浏览器不渲染「打开缓存目录」宿主按钮；刷新快捷键仍重读库存。
  await expect(page.getByRole("button", { name: /打开 .* 缓存目录/ })).toHaveCount(0);
  const count = await page.evaluate(() => window.__desktopFixture.calls.filter((call) => call.method === "inventory").length);
  await page.keyboard.press("F5");
  await expect.poll(() => page.evaluate(() => window.__desktopFixture.calls.filter((call) => call.method === "inventory").length)).toBe(count + 1);
});

test("a known absence of physically burnable targets cannot publish residual color diagnostics", async ({ page }) => {
  await fixture(page);
  await page.evaluate(() => { window.__desktopFixture.report.dominant_burnable_pressure_family = null; });
  await startForecast(page);
  const cloud = page.getByRole("region", { name: "云场与色彩诊断" });
  await expect(cloud).toContainText("无可烧目标云");
  await expect(cloud).not.toContainText("橙红");
  await expect(cloud).not.toContainText("正在增强");
  await expect(cloud.getByText("不适用", { exact: true })).toHaveCount(2);
});


