import { test, expect } from "@playwright/test";

import { IN_PAGE_RPC_MOCK } from "./helpers/in-page-rpc.mjs";

async function fixture(page) {
  await page.addInitScript(IN_PAGE_RPC_MOCK);
  await page.addInitScript(() => {
    const jobs = new Map(), pending = [];
    const requests = [];
    let generation = 1, failPublications = false;
    const progress = (job, event) => job.progress(event);
    const dataReport = (ready) => ({
      ready, datasets: ["gfs", "ecmwf", "cams"].map((source) => ({
        source, ready, missing_count: ready ? 0 : 2,
        verified: ready, is_latest: ready, request_cycle: "2026-11-11 00Z",
        latest_available_cycle: ready ? "2026-11-11 00Z" : "未核验",
        missing_hours: ready || source === "cams" ? [] : [0, 3],
        missing_valid_times: !ready && source === "cams" ? ["2026-11-11T12:00:00+00:00"] : [],
      })),
    });
    let checkReport = dataReport(false);
    const partialResult = {
      model_results: { gfs: {
        peak_quality_index: 0, quality_level: "fixture zero", best_time_local: "2026-11-11T18:10:00",
        timing_window: { start_time_local: "2026-11-11T18:00:00", end_time_local: "2026-11-11T18:20:00" },
        confidence_score: 0, model_disagreement_score: 0, duration_minutes: 0,
        aerosol: { aod550: 0, source: "cams" },
        quality_components: { burnable_cloud_coverage_effective: 0, cloud_material: 0, brightness_color: 0 },
        steps: [
          { valid_time_local: "2026-11-11T18:00:00+08:00", instant_quality_index: 0, solar_elevation_deg: 0, valid: true },
          { valid_time_local: "2026-11-11T18:05:00+08:00", instant_quality_index: null, solar_elevation_deg: -1, valid: false },
          { valid_time_local: "2026-11-11T18:10:00+08:00", instant_quality_index: .8, solar_elevation_deg: -2, valid: true },
          { valid_time_local: "2026-11-11T18:15:00+08:00", instant_quality_index: .9, solar_elevation_deg: -3, valid: true },
        ],
      } },
      model_errors: { ec: "fixture EC upstream unavailable" },
    };
    const active = (method) => [...jobs.values()].findLast((job) => job.method === method && !job.settled);
    let idCounter = 0;
    window.__workFixture = {
      requests,
      partialResult,
      setCheckReport(value) { checkReport = value; },
      failPublications() { failPublications = true; },
      hasPending(method) { return active(method) !== undefined; },
      finishPrepare() { active("prepare")?.resolve({ session: "work-session", report: dataReport(true) }); },
      finishForecast(result = partialResult) { active("forecast")?.resolve(result); },
      failForecast() { active("forecast")?.reject({ code: "operation_failed", message: "fixture second forecast failed" }); },
      pendingCount(method) { return pending.filter((item) => item.method === method).length; },
      finishLocation(method, index, point) {
        const target = pending.filter((item) => item.method === method)[index];
        if (target) target.resolve(point);
      },
    };
    window.__rpcStatus = () => ({ generation });
    window.__rpcSubmit = (job) => {
      requests.push({ method: job.method, params: job.params });
      jobs.set(job.id, job);
      if (job.method === "check") job.resolve({ session: "work-session", report: checkReport });
      if (job.method === "prepare") progress(job, { percent: 49, message: "fixture GFS preparation" });
      if (job.method === "search" || job.method === "metadata") pending.push(job);
    };
    window.__rpcCancelJob = (job) => {
      requests.push({ method: "cancel", params: { requestId: job.id } });
      job.reject({ code: "cancelled", message: "fixture cancelled" });
      return true;
    };
    // 直接 RPC 方法经 __rpcDirect 同步应答（IN_PAGE_RPC_MOCK 注册为即时完成的 job）。
    window.__rpcDirect = (method, params) => {
      if (method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [
        { code: "east-asia", label: "东亚" }, { code: "china", label: "中国" },
      ] };
      if (method === "inventory") return { datasets: [] };
      if (method === "cams_credentials") return { configured: false, can_save: true };
      if (method === "dismiss") return { dismissed: true };
      if (method === "publications") {
        if (failPublications) throw Object.assign(new Error("fixture publication verification failed"), { code: "operation_failed" });
        return { session: "work-session", report: dataReport(false) };
      }
      // search/metadata 也是未完成任务：undefined 交给 __rpcSubmit，由 finishLocation
      // 经 job.resolve 应答（pending 由 __rpcSubmit 侧注册）。
      return undefined;
    };
  });
  await page.route(/https:\/\/(?:[^/]+\.)?(?:openstreetmap\.org|tianditu\.gov\.cn|arcgisonline\.com)\//, (route) => route.abort());
  await page.goto("/?mapOffline=1");
  await expect(page.getByRole("heading", { name: "单点预测", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始预测", exact: true })).toBeEnabled();
}

async function forecast(page) {
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await expect(dialog.getByRole("button", { name: "确认预测", exact: true })).toBeDisabled();
  await expect(dialog.getByText("待下载", { exact: true })).toHaveCount(3);
  await expect(dialog.getByText("所需周期 UTC：2026-11-11 00Z", { exact: true })).toHaveCount(3);
  await expect(dialog.getByText("缺失预报时效：F000、F003", { exact: true })).toHaveCount(2);
  await expect(dialog.getByText("缺失有效时次 UTC：2026-11-11T12:00:00+00:00", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.__workFixture.requests.filter((item) => ["prepare", "forecast"].includes(item.method)))).toHaveLength(0);
  await dialog.getByRole("button", { name: "下载缺失数据", exact: true }).click();
  await expect(dialog.getByText("fixture GFS preparation", { exact: true })).toBeVisible();
  await page.evaluate(() => window.__workFixture.finishPrepare());
  await expect(dialog.getByRole("button", { name: "确认预测", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => window.__workFixture.requests.filter((item) => item.method === "forecast"))).toHaveLength(0);
  await dialog.getByRole("button", { name: "确认预测", exact: true }).click();
  // HTTP 任务通道异步注册 job：等 forecast 出现后再结束它。
  await expect.poll(() => page.evaluate(() => window.__workFixture.hasPending("forecast"))).toBe(true);
  await page.evaluate(() => window.__workFixture.finishForecast());
  await expect(dialog).not.toBeVisible();
  await expect(page.locator(".model-summary").first().locator(".score b")).toHaveText("0.00");
}

test("a complete older cycle needs a separate explicit acknowledgement", async ({ page }) => {
  await fixture(page);
  await page.evaluate(() => window.__workFixture.setCheckReport({
    ready: true,
    datasets: [{
      source: "gfs", ready: true, missing_count: 0, missing_hours: [],
      request_cycle: "2026-11-10 00Z", verified: true, is_latest: false,
      latest_available_cycle: "2026-11-11 00Z",
    }],
  }));
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await expect(dialog.getByText("所需周期 UTC：2026-11-10 00Z", { exact: true })).toBeVisible();
  await expect(dialog.getByText("最新发布 UTC：2026-11-11 00Z · 有更新周期", { exact: true })).toBeVisible();
  const confirm = dialog.getByRole("button", { name: "确认预测", exact: true });
  await expect(confirm).toBeDisabled();
  const acknowledgement = dialog.getByLabel("已有数据源确认存在更新周期；我了解当前周期较旧，仍使用本次可用数据。", { exact: true });
  await acknowledgement.check();
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect.poll(() => page.evaluate(() => window.__workFixture.requests.findLast((row) => row.method === "forecast")?.params)).toEqual({
    session: "work-session", confirmed: true, cycle_age_confirmed: true,
  });
  await expect.poll(() => page.evaluate(() => window.__workFixture.hasPending("forecast"))).toBe(true);
  await page.evaluate(() => window.__workFixture.finishForecast());
  await expect(dialog).not.toBeVisible();
});

test("preflight remains usable in a short window with long content and announces errors", async ({ page }, testInfo) => {
  await fixture(page);
  await page.setViewportSize({ width: 390, height: 520 });
  const longName = `Location_${"x".repeat(120)}`;
  const missingHours = Array.from({ length: 20 }, (_, index) => index * 3);
  const missingTimes = Array.from({ length: 20 }, (_, index) => `2026-11-11T${String(index).padStart(2, "0")}:00:00+00:00`);
  await page.getByRole("button", { name: "编辑坐标", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill(longName);
  await page.evaluate(({ missingHours, missingTimes }) => {
    window.__workFixture.setCheckReport({
      ready: false,
      datasets: ["gfs", "ecmwf", "cams"].map((source) => ({
        source, ready: false, missing_count: 20, verified: true, is_latest: true,
        request_cycle: "2026-11-11 00Z", latest_available_cycle: "2026-11-11 00Z",
        missing_hours: source === "cams" ? [] : missingHours,
        missing_valid_times: source === "cams" ? missingTimes : [],
      })),
    });
  }, { missingHours, missingTimes });
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await expect(dialog.locator("header p")).toContainText(longName);
  const footer = dialog.locator("footer");
  const [dialogBounds, footerBounds] = await Promise.all([dialog.boundingBox(), footer.boundingBox()]);
  expect(dialogBounds).toBeTruthy();
  expect(footerBounds).toBeTruthy();
  expect(footerBounds.x).toBeGreaterThanOrEqual(0);
  expect(footerBounds.x + footerBounds.width).toBeLessThanOrEqual(390);
  expect(footerBounds.y).toBeGreaterThanOrEqual(0);
  expect(footerBounds.y + footerBounds.height).toBeLessThanOrEqual(520);
  const actionHeights = await dialog.locator(".preflight-secondary button, footer button").evaluateAll((buttons) =>
    buttons.map((button) => button.getBoundingClientRect().height));
  expect(actionHeights.every((height) => height >= 44)).toBe(true);
  await expect(dialog.getByRole("button", { name: "确认预测", exact: true })).toBeDisabled();
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("preflight-390x520-light.png"), animations: "disabled" });
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
  await page.screenshot({ path: testInfo.outputPath("preflight-390x520-dark.png"), animations: "disabled" });
  await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
  await dialog.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  const [lastRowBounds, scrolledFooterBounds] = await Promise.all([
    dialog.locator(".preflight-row").nth(2).boundingBox(), footer.boundingBox(),
  ]);
  expect(lastRowBounds).toBeTruthy();
  expect(scrolledFooterBounds).toBeTruthy();
  expect(lastRowBounds.y + lastRowBounds.height).toBeLessThanOrEqual(scrolledFooterBounds.y);
  await page.screenshot({ path: testInfo.outputPath("preflight-390x520-light-bottom.png"), animations: "disabled" });

  await page.evaluate(() => window.__workFixture.failPublications());
  await dialog.getByRole("button", { name: "核验最新发布", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("fixture publication verification failed");
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole("button", { name: "开始预测", exact: true })).toBeFocused();

  await page.setViewportSize({ width: 1440, height: 720 });
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  const desktopDialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await expect(desktopDialog.getByText("缺失预报时效：", { exact: false }).first()).toBeVisible();
  const desktopBounds = await desktopDialog.boundingBox();
  expect(desktopBounds).toBeTruthy();
  expect(desktopBounds.x).toBeGreaterThanOrEqual(0);
  expect(desktopBounds.x + desktopBounds.width).toBeLessThanOrEqual(1440);
  expect(desktopBounds.y).toBeGreaterThanOrEqual(0);
  expect(desktopBounds.y + desktopBounds.height).toBeLessThanOrEqual(720);
  await page.screenshot({ path: testInfo.outputPath("preflight-1440x720-light.png"), animations: "disabled" });
});

test("published identity and report survive edited parameters and failure until a full successful replacement", async ({ page }, testInfo) => {
  await page.addInitScript(() => {
    window.__openedReports = [];
    window.open = (path) => { window.__openedReports.push(path); return null; };
  });
  await fixture(page);
  await page.evaluate(() => { window.__workFixture.partialResult.html_report_url = `/api/v1/firesky/reports/${"a".repeat(64)}`; });
  await forecast(page);
  const context = page.locator(".forecast-results .context");
  const firstContext = await context.textContent();
  const publication = page.locator(".report-actions time");
  const firstPublishedAt = await publication.getAttribute("datetime");
  const firstPublishedText = await publication.textContent();
  const chart = page.getByRole("img", { name: "质量指数时间曲线", exact: true });
  const firstTimes = await chart.locator("circle title").allTextContents();
  await page.getByRole("button", { name: "编辑坐标", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill("纽约");
  await page.getByLabel("时区", { exact: true }).fill("America/New_York");
  await page.getByLabel("已确认位置数据", { exact: true }).check();
  await page.getByRole("button", { name: "自定义", exact: true }).click();
  await page.locator('input[type="date"][aria-label="目标日期"]').fill("2026-11-15");
  await page.getByRole("button", { name: "朝霞", exact: true }).click();
  await expect(context).toHaveText(firstContext);
  await expect(publication).toHaveText(firstPublishedText);
  expect(await chart.locator("circle title").allTextContents()).toEqual(firstTimes);
  await page.getByRole("button", { name: "区域预测", exact: true }).click();
  await expect(page.locator(".regional-summaries")).toContainText("等待预测");
  await page.getByRole("button", { name: "实况观测", exact: true }).click();
  await expect(page.getByRole("region", { name: "实况摘要", exact: true })).toContainText("等待分析");
  await page.getByRole("button", { name: "单点预测", exact: true }).click();
  await expect(context).toHaveText(firstContext);
  await expect(publication).toHaveAttribute("datetime", firstPublishedAt);
  const beginNext = async () => {
    await page.getByRole("button", { name: "开始预测", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
    await dialog.getByRole("button", { name: "下载缺失数据", exact: true }).click();
    await expect(dialog.getByText("fixture GFS preparation", { exact: true })).toBeVisible();
    await page.evaluate(() => window.__workFixture.finishPrepare());
    await expect(dialog.getByRole("button", { name: "确认预测", exact: true })).toBeEnabled();
    await expect(context).toHaveText(firstContext);
    await dialog.getByRole("button", { name: "确认预测", exact: true }).click();
  };
  await beginNext();
  await expect.poll(() => page.evaluate(() => window.__workFixture.hasPending("forecast"))).toBe(true);
  await page.evaluate(() => window.__workFixture.failForecast());
  await expect(page.getByRole("alert")).toContainText("fixture second forecast failed");
  await expect(context).toHaveText(firstContext);
  await expect(page.locator(".score b").first()).toHaveText("0.00");
  await page.getByRole("button", { name: "打开完整诊断报告", exact: true }).click();
  expect(await page.evaluate(() => window.__openedReports.at(-1))).toBe(`/api/v1/firesky/reports/${"a".repeat(64)}`);
  await beginNext();
  await expect.poll(() => page.evaluate(() => window.__workFixture.hasPending("forecast"))).toBe(true);
  await page.evaluate(() => window.__workFixture.finishForecast({
    model_results: { gfs: { peak_quality_index: 1.25, steps: [] } }, html_report_url: `/api/v1/firesky/reports/${"b".repeat(64)}`,
  }));
  await expect(context).toHaveText("纽约 · 2026-11-15 朝霞");
  await expect(publication).toContainText("America/New_York");
  await expect(page.locator(".score b").first()).toHaveText("1.25");
  await expect(page.getByText("fixture EC upstream unavailable", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "打开完整诊断报告", exact: true }).click();
  expect(await page.evaluate(() => window.__openedReports.at(-1))).toBe(`/api/v1/firesky/reports/${"b".repeat(64)}`);
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 720 });
    for (const theme of ["light", "dark"]) {
      await page.evaluate((theme) => { document.documentElement.dataset.theme = theme; }, theme);
      await publication.evaluate((element) => element.scrollIntoView({ block: "start", inline: "nearest" }));
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      await page.screenshot({ path: testInfo.outputPath(`published-result-${width}-${theme}.png`), animations: "disabled" });
    }
  }
});

test("Swift workbench is the first screen with four business pages, a 292px rail and real empty result views", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await fixture(page);
  const navigation = page.getByRole("navigation", { name: "主要导航" });
  await expect(navigation.getByRole("button")).toHaveText(["单点预测", "区域预测", "实况观测", "数据与下载", "任务历史", "设置"]);
  await expect(navigation.getByRole("button", { name: "地图选点", exact: true })).toHaveCount(0);
  await expect(page.locator(".model-summary")).toHaveCount(2);
  await expect(page.locator(".metric")).toHaveCount(9);
  expect(await page.locator(".control-rail").evaluate((element) => element.getBoundingClientRect().width)).toBe(292);
  await expect(page.getByRole("tab")).toHaveText(["概览", "大气剖面", "诊断与时间窗", "逐时台账"]);
  await page.getByRole("tab", { name: "逐时台账", exact: true }).click();
  await expect(page.getByRole("tabpanel", { name: "逐时台账", exact: true }).getByText("等待预测", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "明天", exact: true }).click();
  await expect(page.getByRole("button", { name: "明天", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "自定义", exact: true }).click();
  await page.locator('input[type="date"][aria-label="目标日期"]').fill("2026-11-14");
  await page.getByRole("button", { name: "朝霞", exact: true }).click();
  await page.getByRole("button", { name: "收藏当前点", exact: true }).click();
  await page.getByRole("group", { name: "地点视图", exact: true }).getByRole("button", { name: "收藏", exact: true }).click();
  await expect(page.locator(".favorites").getByRole("button", { name: "杭州", exact: true })).toBeVisible();
  await page.locator(".favorites").getByRole("button", { name: "删除收藏 杭州", exact: true }).click();
  await expect(page.locator(".favorites")).toContainText("暂无收藏地点");
  await expect(page.locator(".favorites").getByRole("button", { name: "杭州", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => window.__workFixture.requests.filter((item) => ["check", "publications", "prepare", "forecast", "network_probe"].includes(item.method)))).toEqual([]);
});

test("forecast requires preparation and explicit confirmation, preserves zero and one-model failure, and breaks missing-data curves", async ({ page }) => {
  await fixture(page);
  await forecast(page);
  expect(await page.evaluate(() => window.__workFixture.requests.filter((item) => item.method === "forecast").map((item) => item.params))).toEqual([{ session: "work-session", confirmed: true }]);
  await expect(page.locator(".model-summary").nth(1)).toContainText("未完成");
  await expect(page.locator(".model-summary").nth(1).locator(".score b")).toHaveText("--");
  await expect(page.locator(".model-summary").nth(1)).toContainText("fixture EC upstream unavailable");
  const chart = page.getByRole("img", { name: "质量指数时间曲线", exact: true });
  await expect(chart.locator("polyline")).toHaveCount(2);
  await expect(chart.locator("circle")).toHaveCount(3);
  expect(await chart.locator("polyline").evaluateAll((lines) => lines.map((line) => line.getAttribute("points").split(" ").length))).toEqual([1, 2]);
  await page.getByRole("tab", { name: "逐时台账", exact: true }).click();
  const rows = page.getByRole("tabpanel", { name: "逐时台账", exact: true }).locator("tbody tr");
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(0)).toContainText("0.000");
  await expect(rows.nth(1)).toContainText("缺测");
  await expect(rows.nth(1)).toContainText("--");
  await page.getByRole("group", { name: "结果模式", exact: true }).getByRole("button", { name: "EC", exact: true }).click();
  await expect(page.getByRole("tabpanel", { name: "逐时台账", exact: true })).toContainText("EC 未完成：fixture EC upstream unavailable");
});

test("late search and metadata responses cannot overwrite newer coordinates, and zero elevation remains valid", async ({ page }) => {
  await fixture(page);
  await page.getByLabel("搜索地名", { exact: true }).fill("旧地点");
  await page.getByLabel("搜索地名", { exact: true }).press("Enter");
  await page.getByLabel("搜索地名", { exact: true }).fill("新地点");
  await page.getByLabel("搜索地名", { exact: true }).press("Enter");
  await expect.poll(() => page.evaluate(() => window.__workFixture.pendingCount("search"))).toBeGreaterThan(1);
  await page.evaluate(() => window.__workFixture.finishLocation("search", 1, { name: "新地点", lat: 32, lon: 121, elevation_m: 0, timezone_name: "Asia/Shanghai" }));
  await expect(page.locator(".location-name strong")).toHaveText("新地点");
  await expect.poll(() => page.evaluate(() => window.__workFixture.pendingCount("search"))).toBeGreaterThan(0);
  await page.evaluate(() => window.__workFixture.finishLocation("search", 0, { name: "旧地点", lat: 25, lon: 100, elevation_m: 200, timezone_name: "Asia/Shanghai" }));
  await expect(page.locator(".location-name strong")).toHaveText("新地点");
  await expect(page.locator(".location-meta")).toContainText("海拔 0 m");
  await page.getByRole("button", { name: "编辑坐标", exact: true }).click();
  await page.getByLabel("纬度", { exact: true }).fill("33");
  await page.getByRole("button", { name: "获取海拔与时区", exact: true }).click();
  await page.getByLabel("纬度", { exact: true }).fill("34");
  await expect.poll(() => page.evaluate(() => window.__workFixture.pendingCount("metadata"))).toBeGreaterThan(0);
  await page.evaluate(() => window.__workFixture.finishLocation("metadata", 0, { lat: 33, lon: 121, elevation_m: 100, timezone_name: "Asia/Shanghai" }));
  await expect(page.getByLabel("纬度", { exact: true })).toHaveValue("34");
  await expect(page.getByLabel("已确认位置数据", { exact: true })).not.toBeChecked();
  await page.getByRole("button", { name: "获取海拔与时区", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__workFixture.pendingCount("metadata"))).toBeGreaterThan(1);
  await page.evaluate(() => window.__workFixture.finishLocation("metadata", 1, { lat: 34, lon: 121, elevation_m: 0, timezone_name: "Asia/Shanghai" }));
  await expect(page.getByLabel("已确认位置数据", { exact: true })).toBeChecked();
  await expect(page.locator(".location-meta")).toContainText("海拔 0 m");
});

test("fresh geolocation applies its metadata response before allowing a forecast", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: {
        getCurrentPosition(success) {
          queueMicrotask(() => success({
            timestamp: Date.now(),
            coords: { latitude: 31.2304, longitude: 121.4737, accuracy: 5 },
          }));
        },
      },
    });
  });
  await fixture(page);
  await page.getByRole("button", { name: "获取当前位置", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__workFixture.requests.filter((item) => item.method === "metadata").map((item) => item.params))).toEqual([{ lat: 31.2304, lon: 121.4737, elevation_source: "auto" }]);
  await expect(page.locator(".location-meta")).toContainText("海拔未确认");
  await expect(page.locator(".location-name strong")).toHaveText("当前位置");
  await expect(page.getByRole("button", { name: "开始预测", exact: true })).toBeDisabled();
  await expect.poll(() => page.evaluate(() => window.__workFixture.pendingCount("metadata"))).toBeGreaterThan(0);
  await page.evaluate(() => window.__workFixture.finishLocation("metadata", 0, {
    lat: 31.2304, lon: 121.4737, elevation_m: 4, timezone_name: "Asia/Shanghai",
  }));
  await expect(page.locator(".location-name strong")).toHaveText("当前位置");
  await expect(page.locator(".location-name .coordinates")).toContainText("31.2304");
  await expect(page.locator(".location-meta")).toContainText("海拔 4 m");
  await expect(page.getByRole("button", { name: "开始预测", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "预测准备", exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__workFixture.requests.findLast((item) => item.method === "check")?.params.payload)).toMatchObject({
    lat: 31.2304, lon: 121.4737, elevation_m: 4, timezone_name: "Asia/Shanghai",
  });
});

test("today and tomorrow follow the observation timezone while custom dates survive location changes", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-11T01:00:00Z"));
  await fixture(page);
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  const checkDate = async (expected) => {
    await page.getByRole("button", { name: "开始预测", exact: true }).click();
    await expect(dialog).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.__workFixture.requests.findLast((item) => item.method === "check")?.params.payload.target_date)).toBe(expected);
    await dialog.getByRole("button", { name: "返回", exact: true }).click();
    await expect(dialog).not.toBeVisible();
  };
  await checkDate("2026-11-11");
  await page.getByLabel("搜索地名", { exact: true }).fill("Los Angeles");
  await page.getByLabel("搜索地名", { exact: true }).press("Enter");
  await expect.poll(() => page.evaluate(() => window.__workFixture.pendingCount("search"))).toBeGreaterThan(0);
  await page.evaluate(() => window.__workFixture.finishLocation("search", 0, {
    name: "Los Angeles", lat: 34.05, lon: -118.25, elevation_m: 71, timezone_name: "America/Los_Angeles",
  }));
  await expect(page.locator(".location-name strong")).toHaveText("Los Angeles");
  await expect(page.locator(".location-meta")).toContainText("America/Los_Angeles");
  await expect(page.getByRole("button", { name: "今天", exact: true })).toHaveAttribute("aria-pressed", "true");
  await checkDate("2026-11-10");
  await page.getByRole("button", { name: "明天", exact: true }).click();
  await checkDate("2026-11-11");
  await page.getByRole("button", { name: "自定义", exact: true }).click();
  await page.locator('input[type="date"][aria-label="目标日期"]').fill("2026-11-17");
  await page.getByLabel("搜索地名", { exact: true }).fill("杭州");
  await page.getByLabel("搜索地名", { exact: true }).press("Enter");
  await expect.poll(() => page.evaluate(() => window.__workFixture.pendingCount("search"))).toBeGreaterThan(1);
  await page.evaluate(() => window.__workFixture.finishLocation("search", 1, {
    name: "杭州", lat: 30.2741, lon: 120.1551, elevation_m: 8, timezone_name: "Asia/Shanghai",
  }));
  await expect(page.locator(".location-name strong")).toHaveText("杭州");
  await expect(page.locator(".location-meta")).toContainText("Asia/Shanghai");
  await expect(page.locator('input[type="date"][aria-label="目标日期"]')).toHaveValue("2026-11-17");
  await checkDate("2026-11-17");
});

test("Swift workbench populated views fit narrow and wide windows in both themes", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await forecast(page);
  for (const [width, height] of [[390, 667], [768, 720], [1024, 720], [1440, 900]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ["light", "dark"]) {
      await page.evaluate(async (value) => {
        document.documentElement.dataset.theme = value;
        document.querySelector(".workspace").scrollTop = 0;
        await document.fonts.ready;
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }, theme);
      await expect(page.locator(".brand")).toHaveCount(1);
      await expect(page.locator(".topbar")).toHaveCount(1);
      const bounds = await page.evaluate(() => ({
        document: document.documentElement.scrollWidth > innerWidth,
        workspace: document.querySelector(".workspace").scrollWidth > document.querySelector(".workspace").clientWidth,
        clipped: [...document.querySelectorAll(".control-rail input,.control-rail button,.analysis-tabs button,.model-summary")].filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.width && rect.height && (rect.left < 0 || rect.right > innerWidth + 1);
        }).map((element) => element.textContent),
      }));
      expect(bounds).toEqual({ document: false, workspace: false, clipped: [] });
      if (width <= 600) {
        const panelWidths = await page.evaluate(() => ({
          rail: document.querySelector(".control-rail").getBoundingClientRect().width,
          location: document.querySelector(".location-panel").getBoundingClientRect().width,
          parameters: document.querySelector(".parameter-panel").getBoundingClientRect().width,
        }));
        expect(panelWidths.location).toBeCloseTo(panelWidths.rail, 0);
        expect(panelWidths.parameters).toBeCloseTo(panelWidths.rail, 0);
      }
      await page.screenshot({ path: testInfo.outputPath(`workbench-${width}-${theme}.png`), fullPage: true, animations: "disabled", caret: "hide" });
    }
  }
  expect(errors).toEqual([]);
});

test("empty coordinate edits stay renderable and manually confirmed invalid coordinates cannot start a check", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await page.getByRole("button", { name: "编辑坐标", exact: true }).click();
  await page.getByLabel("纬度", { exact: true }).fill("");
  await expect(page.getByRole("heading", { name: "单点预测", exact: true })).toBeVisible();
  await expect(page.getByLabel("已确认位置数据", { exact: true })).not.toBeChecked();
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "预测准备", exact: true })).not.toBeVisible();
  expect(await page.evaluate(() => window.__workFixture.requests.filter((item) => item.method === "check"))).toHaveLength(0);
  await page.getByLabel("纬度", { exact: true }).fill("91");
  await page.getByLabel("已确认位置数据", { exact: true }).check();
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "预测准备", exact: true })).not.toBeVisible();
  expect(await page.evaluate(() => window.__workFixture.requests.filter((item) => item.method === "check"))).toHaveLength(0);
  expect(errors).toEqual([]);
});

test("appearance and EC source use Swift settings and survive reload without persisting map credentials", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByLabel("主题", { exact: true }).selectOption("dark");
  await page.getByLabel("EC 下载源", { exact: true }).selectOption("google");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByLabel(/^天地图浏览器 Key/).fill("fixture-public-key-kept-only-in-memory");
  await page.getByRole("button", { name: "应用地图凭据", exact: true }).click();
  await page.reload();
  await expect(page.getByRole("heading", { name: "单点预测", exact: true })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByLabel("主题", { exact: true })).toHaveValue("dark");
  await expect(page.getByLabel("EC 下载源", { exact: true })).toHaveValue("google");
  await expect(page.getByLabel(/^天地图浏览器 Key/)).toHaveValue("");
  expect(await page.evaluate(() => JSON.stringify(localStorage).includes("fixture-public-key-kept-only-in-memory"))).toBe(false);
});

test("elevation source setting passes through search/metadata and the response source is displayed", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByLabel("海拔来源", { exact: true }).selectOption("srtm");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("firesky.elevationSource"))).toBe("srtm");
  await page.getByRole("button", { name: "单点预测", exact: true }).click();
  await page.getByLabel("搜索地名", { exact: true }).fill("杭州");
  await page.getByLabel("搜索地名", { exact: true }).press("Enter");
  await expect.poll(() => page.evaluate(() => window.__workFixture.requests.findLast((item) => item.method === "search")?.params)).toEqual({ query: "杭州", elevation_source: "srtm" });
  await page.evaluate(() => window.__workFixture.finishLocation("search", 0, { name: "杭州", lat: 30.2741, lon: 120.1551, elevation_m: 8, timezone_name: "Asia/Shanghai", source: "local-timezone+srtm" }));
  await expect(page.locator(".location-meta")).toContainText("海拔来源：本地时区 + SRTM");
  await page.getByRole("button", { name: "编辑坐标", exact: true }).click();
  await page.getByRole("button", { name: "获取海拔与时区", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__workFixture.requests.findLast((item) => item.method === "metadata")?.params)).toEqual({ lat: 30.2741, lon: 120.1551, elevation_source: "srtm" });
  await page.evaluate(() => window.__workFixture.finishLocation("metadata", 0, { lat: 30.2741, lon: 120.1551, elevation_m: 8, timezone_name: "Asia/Shanghai", source: "open-meteo-point-metadata" }));
  await expect(page.locator(".location-meta")).toContainText("海拔来源：open-meteo 在线");
});
