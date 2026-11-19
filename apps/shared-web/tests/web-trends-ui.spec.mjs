import { test, expect } from "@playwright/test";
import { IN_PAGE_RPC_MOCK } from "./helpers/in-page-rpc.mjs";

async function fixture(page) {
  await page.addInitScript(IN_PAGE_RPC_MOCK);
  await page.addInitScript(() => {
    localStorage.setItem("firesky.appearance", "light");
    const step = (minute, extra) => ({
      valid: true,
      valid_time_local: `2026-11-11T18:${String(minute).padStart(2, "0")}:00+08:00`,
      ...extra,
    });
    const result = {
      model_results: {
        gfs: {
          peak_quality_index: 1, confidence_score: 0, best_time_local: "2026-11-11T18:15:00+08:00",
          steps: [
            step(0, {
              instant_quality_index: 0, quality_aod550: 0,
              forward_view_cloud_field_amount: 0, burnable_cloud_coverage: 0,
              forward_burnable_cloud_field_amount: 0, forward_burnable_cloud_ratio: 0,
              observer_return_transmission: 1, solar_incoming_block_risk: 0,
            }),
            step(5, {
              valid: false, instant_quality_index: 1, quality_aod550: .8,
              forward_view_cloud_field_amount: .8, burnable_cloud_coverage: .8,
            }),
            step(10, {
              instant_quality_index: .5, quality_aod550: null,
              forward_view_cloud_field_amount: .7, burnable_cloud_coverage: .5,
              forward_burnable_cloud_field_amount: .4, forward_burnable_cloud_ratio: .6,
              observer_return_transmission: null, solar_incoming_block_risk: .3,
            }),
            step(15, {
              instant_quality_index: 1, quality_aod550: .2,
              forward_view_cloud_field_amount: .9, burnable_cloud_coverage: .8,
              forward_burnable_cloud_field_amount: .6, forward_burnable_cloud_ratio: .7,
              observer_return_transmission: .8, solar_incoming_block_risk: .1,
            }),
          ],
        },
      },
      model_errors: { ec: "fixture EC unavailable" },
    };
    window.__rpcStatus = () => ({ generation: 1 });
    window.__rpcDirect = (method) => {
      if (method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [] };
      if (method === "cams_credentials") return { configured: false, can_save: true };
      if (method === "dismiss") return { dismissed: true };
      if (method === "inventory") return { datasets: [] };
      return undefined;
    };
    window.__rpcSubmit = (job) => {
      if (job.method === "check") job.resolve({ session: "trend-fixture", report: { ready: true, datasets: [] } });
      else if (job.method === "forecast") job.resolve(result);
      else throw new Error(`Unexpected fixture method ${job.method}`);
    };
  });
  await page.goto("/?mapOffline=1");
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await dialog.getByRole("button", { name: "确认预测", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  return page.getByRole("region", { name: "云况与光学趋势", exact: true });
}

test("Web physical trends preserve gaps, zero readings and selectable series and times", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const trends = await fixture(page);
  await expect(trends).toBeVisible();
  const plot = trends.getByRole("img", { name: "逐时云量、可烧比例与光路占比曲线", exact: true });
  await expect(trends.getByRole("checkbox")).toHaveCount(6);
  await expect(plot.locator("polyline")).toHaveCount(12);
  const aod = trends.getByRole("img", { name: "AOD550 逐时光学厚度曲线", exact: true });
  await expect(aod.locator("polyline")).toHaveCount(2);
  await expect(aod.locator("circle")).toHaveCount(2);
  await trends.getByRole("slider", { name: "逐时时刻", exact: true }).focus();
  await trends.getByRole("slider", { name: "逐时时刻", exact: true }).press("Home");
  await expect(trends.locator("output")).toHaveText("18:00 · 质量 0.000");
  const readings = trends.locator(".readings > div");
  await expect(readings.filter({ has: page.locator("dt", { hasText: /^前方总云量$/ }) }).locator("dd")).toHaveText("0%");
  await expect(readings.filter({ has: page.locator("dt", { hasText: /^AOD550$/ }) }).locator("dd")).toHaveText("0.000");
  await trends.getByRole("button", { name: "下一时次", exact: true }).click();
  await expect(trends.locator("output")).toHaveText("18:05 · 缺测");
  await expect(trends.locator(".readings dd")).toHaveText(Array(7).fill("--"));
  await trends.getByRole("checkbox", { name: "可烧云占比", exact: true }).uncheck();
  await expect(plot.locator("polyline")).toHaveCount(10);
  await trends.getByRole("checkbox", { name: "可烧云占比", exact: true }).check();
  await expect(plot.locator("polyline")).toHaveCount(12);
  expect(errors).toEqual([]);
});

test("Web physical trend content fits small and wide windows in both themes", async ({ page }) => {
  const trends = await fixture(page);
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 820 });
    for (const theme of ["light", "dark"]) {
      await page.evaluate(async (value) => {
        document.documentElement.dataset.theme = value;
        await document.fonts.ready;
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }, theme);
      await trends.scrollIntoViewIfNeeded();
      await expect(trends).toBeVisible();
      expect(await trends.evaluate((root) => root.scrollWidth <= root.clientWidth + 1)).toBe(true);
      expect(await trends.getByRole("img").evaluateAll((charts) => charts.length === 2 && charts.every((chart) => {
        const rect = chart.getBoundingClientRect();
        return rect.width > 100 && rect.height >= 200 && rect.left >= 0 && rect.right <= innerWidth + 1;
      }))).toBe(true);
      // 视觉基线（uPlot 试点前置）：替换前 SVG 渲染留档，见 __screenshots__。
      await expect(trends).toHaveScreenshot(`web-trends-${width}-${theme}.png`, {
        animations: "disabled", caret: "hide", maxDiffPixelRatio: 0.02,
      });
    }
  }
});
