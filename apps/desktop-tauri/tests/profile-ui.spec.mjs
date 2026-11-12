import { test, expect } from "@playwright/test";

async function fixture(page, satellite) {
  await page.addInitScript(({ satellite }) => {
    localStorage.clear();
    const tickets = new Map();
    const section = {
      model_source: "gfs", solar_azimuth_deg: 270, solar_elevation_deg: -2, offset_minutes: 5,
      distances_km: [0, 20, 40], pressure_levels_hpa: [850, 500],
      terrain_elevation_asl_m: [0, 100, 200], required_illumination_base_asl_m: [1200, 1600, 2200],
      valid_time_local: "2026-11-11T18:00:00+08:00",
      fields: {
        geopotential_height_asl_m: [[1000, 5000], [1100, 5200], [1200, 5400]],
        cloud_fraction_percent: [[0, null], [40, 70], [20, 90]],
        temperature_c: [[10, -20], [8, -22], [6, -24]], rh_percent: [[60, 80], [70, 90], [80, 90]],
        cloud_layer_status: [[0, 0], [2, 3], [0, 3]],
      },
      theoretical_targets: [{
        distance_km: 20, pressure_family_label: "中云", cloud_phase: { label: "mixed" },
        cloud_base_asl_m: 4000, cloud_top_asl_m: 6000, cloud_base_agl_m: 3900, cloud_top_agl_m: 5900,
        status: "burnable", status_reason: "fixture geometry",
      }],
    };
    const report = {
      peak_quality_index: 0, cross_section: section, steps: [],
      nowcast: {
        satellite_vertical_profile: {
          status: "partial_unknown", coverage: .75, valid_time_utc: "2026-11-11T10:00:00Z", bands: ["B07", "B13", "B15"],
          samples: [
            ...[0, 20, 40].map((distance, column) => ({
              distance_km: distance, bearing_deg: 270, status: "retrieved",
              levels: [1000, 5000].map((height, row) => ({
                height_m: height + column * 100, pressure_hpa: row ? 500 : 850, status: column === 0 && row === 1 ? "unknown" : "retrieved",
                hsd_posterior_percent: column === 0 ? row ? null : 0 : column * 30 + row * 20,
                temperature_c: row ? -20 : 10, rh_percent: 70,
              })),
            })),
            { distance_km: 20, bearing_deg: 250, status: "retrieved", levels: [{ height_m: 4000, pressure_hpa: 600, status: "retrieved", hsd_posterior_percent: 99 }] },
          ],
        },
      },
    };
    let callback = 0;
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
    window.isTauri = true;
    window.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: "main" } },
      transformCallback() { return ++callback; },
      async invoke(command, args = {}) {
        if (command === "plugin:event|listen") return callback;
        if (command === "plugin:event|unlisten") return;
        if (command === "rpc_status") return { running: true, generation: 1, workspace: "/fixture", engineBinary: "/fixture/engine" };
        if (command === "rpc_request") {
          if (args.method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [] };
          if (args.method === "dismiss") return { dismissed: true };
          throw new Error(`Unexpected fixture request ${args.method}`);
        }
        if (command === "rpc_start") {
          if (args.method === "check") tickets.set(args.requestId, { session: "profile-fixture", report: { ready: true, datasets: [] } });
          else if (args.method === "forecast") tickets.set(args.requestId, satellite ? report : { model_results: { gfs: report, ec: { ...report, cross_section: null } } });
          else throw new Error(`Unexpected fixture mutation ${args.method}`);
          return { requestId: args.requestId, generation: 1 };
        }
        if (command === "rpc_wait") return tickets.get(args.requestId);
        throw new Error(`Unexpected fixture command ${command}`);
      },
    };
  }, { satellite });
  await page.goto("/?mapOffline=1");
  if (satellite) await page.getByRole("button", { name: "实况观测", exact: true }).click();
  await expect(page.getByRole("button", { name: satellite ? "开始实况分析" : "开始预测", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: satellite ? "开始实况分析" : "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: satellite ? "实况准备" : "预测准备", exact: true });
  await dialog.getByRole("button", { name: satellite ? "确认分析" : "确认预测", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("tab", { name: "大气剖面", exact: true }).click();
}

test("satellite profile renders one solar bearing with zero, missing, and keyboard-readable evidence", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page, true);
  const chart = page.getByRole("img", { name: "沿太阳方向的卫星云层后验概率剖面", exact: true });
  await expect(chart).toHaveAttribute("data-profile-cells", "6");
  await expect(chart.locator(".profile-cell.missing")).toHaveCount(1);
  await expect(chart.locator(".profile-cell").first()).toHaveAccessibleName(/后验概率 0%/);
  await chart.locator(".profile-cell").first().focus();
  await expect(page.locator(".profile .readout")).toContainText("后验概率 0%");
  await page.keyboard.press("ArrowRight");
  await expect(page.locator(".profile .readout")).toContainText("后验概率 缺测");
  await expect(chart.locator(".pressure-line")).not.toHaveCount(0);
  await expect(chart.locator(".illumination")).toHaveCount(1);
  await expect(page.locator(".profile .source-note")).toContainText("不发布 COT");
  expect(errors).toEqual([]);
});

test("model profiles preserve matrix values and fit narrow and wide workspaces in both themes", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page, false);
  const chart = page.getByRole("img", { name: "沿太阳方向的模式云量剖面", exact: true });
  await expect(chart).toHaveAttribute("data-profile-cells", "6");
  await expect(chart.locator(".profile-cell.missing")).toHaveCount(1);
  await expect(chart.locator(".profile-cell").first()).toHaveAccessibleName(/模式云量 0%/);
  await expect(page.locator(".profile .targets")).toContainText("中云 · 混合相 · 20 km");
  await page.getByRole("group", { name: "结果模式", exact: true }).getByRole("button", { name: "EC", exact: true }).click();
  await expect(page.getByText("暂无可用模式剖面采样", { exact: true })).toBeVisible();
  await page.getByRole("group", { name: "结果模式", exact: true }).getByRole("button", { name: "GFS", exact: true }).click();
  for (const width of [390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 820 });
    for (const theme of ["light", "dark"]) {
      await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
      await chart.scrollIntoViewIfNeeded();
      await expect(chart).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await chart.locator(".profile-cell").evaluateAll((cells) => cells.every((cell) => {
        const box = cell.getBoundingClientRect();
        return box.width > 0 && box.height > 0;
      }))).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`profile-${width}-${theme}.png`), fullPage: true });
    }
  }
  expect(errors).toEqual([]);
});
