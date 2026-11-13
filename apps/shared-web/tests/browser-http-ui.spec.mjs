import { test, expect } from "@playwright/test";

async function fixture(page) {
  const state = { jobs: new Map(), requests: [], prepareComplete: false, cancelComplete: false, forecastFailure: false, forecastResult: null };
  const report = (ready) => ({ ready, datasets: ["gfs", "ecmwf", "cams"].map((source) => ({
    source, ready, missing_count: ready ? 0 : 1, verified: ready, is_latest: ready,
    request_cycle: "2026-11-11 00Z", latest_available_cycle: ready ? "2026-11-11 00Z" : "未核验",
  })) });
  const result = (method) => {
    if (method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [{ code: "east-asia", label: "东亚" }] };
    if (method === "check" || method === "publications") return { session: "fixture-session", report: report(false) };
    if (method === "prepare") return { session: "fixture-session", report: report(true) };
    if (method === "cams_credentials") return { configured: false, can_save: true };
    if (method === "inventory") return { datasets: [{ source: "gfs", path: "/fixture/cache", total_file_count: 0, total_storage_mb: 0, cycles: {}, latest_cycle: null }] };
    if (method === "task_history") return { schema: "firesky-task-history-v1", tasks: [], total: 0, issues: [], automatic_restart: false };
    if (method === "retention_settings") return { schema: "firesky-retention-settings-v1", retain_cycles: 4, retain_satellite_days: 10, retain_reports: 200 };
    if (method === "dismiss") return { dismissed: true };
    if (method === "forecast") return state.forecastResult ?? {
      model_results: { gfs: { peak_quality_index: 0, quality_level: "fixture zero", confidence_score: 0, steps: [] } },
      model_errors: { ec: "fixture unavailable" },
      html_report_path: "/server/private/report.html",
      html_report_url: `/api/v1/firesky/reports/${"a".repeat(64)}`,
    };
    throw new Error(`Unexpected method ${method}`);
  };
  await page.route("**/api/v1/firesky/desktop/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1/firesky/desktop", "");
    const body = request.method() === "POST" ? request.postDataJSON() : null;
    if (path === "/status") return route.fulfill({ json: { instance_id: "browser-fixture", running: true, protocol: 1, engine: "firesky-rust", workspace: "/fixture" } });
    if (path === "/jobs") {
      expect(body.instance_id).toBe("browser-fixture");
      expect(body.request_id).toMatch(/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/);
      state.requests.push(body);
      state.jobs.set(body.request_id, { ...body, cancelRequested: false });
      return route.fulfill({ json: { request_id: body.request_id, instance_id: body.instance_id, status: "running" } });
    }
    const [, , id, action] = path.split("/");
    const job = state.jobs.get(id);
    if (action === "release") {
      state.jobs.delete(id);
      return route.fulfill({ json: { released: true } });
    }
    if (action === "cancel") {
      job.cancelRequested = true;
      return route.fulfill({ json: { requested: true } });
    }
    expect(job).toBeTruthy();
    const pending = job.method === "prepare" && !state.prepareComplete;
    const cancelled = job.cancelRequested && state.cancelComplete;
    const failed = job.method === "forecast" && state.forecastFailure;
    return route.fulfill({ json: {
      request_id: id, instance_id: job.instance_id,
      status: cancelled ? "cancelled" : failed ? "failed" : pending ? "running" : "done", progress_seq: pending ? 1 : 2,
      progress: pending ? { percent: 0, stage: "download", message: "fixture HTTP download" } : { percent: 100, stage: cancelled ? "cancelled" : "done" },
      ...(failed ? { error: { code: "operation_failed", message: "fixture HTTP second forecast failed" } } : !pending && !cancelled ? { result: result(job.method) } : {}),
    } });
  });
  await page.route(/https:\/\/(?:[^/]+\.)?tianditu\.gov\.cn\//, (route) => route.abort());
  await page.goto("/?mapOffline=1");
  await expect(page.getByRole("button", { name: "开始预测", exact: true })).toBeEnabled();
  return state;
}

test("shared browser UI uses HTTP only and keeps preparation and forecast explicitly separate", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const state = await fixture(page);
  expect(await page.evaluate(() => "__TAURI_INTERNALS__" in window)).toBe(false);
  expect(state.requests.some((row) => ["check", "prepare", "forecast"].includes(row.method))).toBe(false);
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await expect(dialog.getByRole("button", { name: "确认预测", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "下载缺失数据", exact: true }).click();
  await expect(dialog.getByText("fixture HTTP download", { exact: true })).toBeVisible();
  expect(state.requests.some((row) => row.method === "forecast")).toBe(false);
  state.prepareComplete = true;
  await expect(dialog.getByRole("button", { name: "确认预测", exact: true })).toBeEnabled();
  await dialog.getByRole("button", { name: "确认预测", exact: true }).click();
  await expect(page.locator(".model-summary").first().locator(".score b")).toHaveText("0.00");
  await expect(page.getByRole("button", { name: "打开完整诊断报告", exact: true })).toBeVisible();
  expect(state.requests.filter((row) => row.method === "forecast").map((row) => row.params)).toEqual([{ session: "fixture-session", confirmed: true }]);
  expect(errors).toEqual([]);
});

test("HTTP published results keep their checked identity and report across failure and reconnect until replacement succeeds", async ({ page }) => {
  await page.addInitScript(() => {
    window.__openedReports = [];
    window.open = (path) => { window.__openedReports.push(path); return null; };
  });
  const state = await fixture(page);
  state.prepareComplete = true;
  const runForecast = async () => {
    await page.getByRole("button", { name: "开始预测", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
    await dialog.getByRole("button", { name: "下载缺失数据", exact: true }).click();
    await expect(dialog.getByRole("button", { name: "确认预测", exact: true })).toBeEnabled();
    await dialog.getByRole("button", { name: "确认预测", exact: true }).click();
  };
  await runForecast();
  const context = page.locator(".forecast-results .context");
  await expect(page.locator(".score b").first()).toHaveText("0.00");
  const firstContext = await context.textContent();
  const publication = page.locator(".report-actions time");
  const firstPublishedAt = await publication.getAttribute("datetime");
  const firstPublishedText = await publication.textContent();
  await page.getByRole("button", { name: "编辑坐标", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill("新请求");
  await page.getByLabel("时区", { exact: true }).fill("America/New_York");
  await page.getByLabel("已确认位置数据", { exact: true }).check();
  await page.getByRole("button", { name: "自定义", exact: true }).click();
  await page.locator('input[type="date"][aria-label="目标日期"]').fill("2026-11-16");
  await page.getByRole("button", { name: "朝霞", exact: true }).click();
  await expect(context).toHaveText(firstContext);
  await expect(publication).toHaveText(firstPublishedText);
  state.forecastFailure = true;
  await runForecast();
  await expect(page.getByRole("alert")).toContainText("fixture HTTP second forecast failed");
  await expect(context).toHaveText(firstContext);
  await expect(publication).toHaveAttribute("datetime", firstPublishedAt);
  await expect(page.locator(".score b").first()).toHaveText("0.00");
  await page.getByRole("button", { name: "打开完整诊断报告", exact: true }).click();
  expect(await page.evaluate(() => window.__openedReports.at(-1))).toBe(`/api/v1/firesky/reports/${"a".repeat(64)}`);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.locator(".engine-badge.ok")).toBeVisible();
  await page.getByRole("button", { name: "单点预测", exact: true }).click();
  await expect(context).toHaveText(firstContext);
  state.forecastFailure = false;
  state.forecastResult = {
    model_results: { gfs: { peak_quality_index: 2, steps: [] } },
    html_report_url: `/api/v1/firesky/reports/${"b".repeat(64)}`,
  };
  await runForecast();
  await expect(context).toHaveText("新请求 · 2026-11-16 朝霞");
  await expect(publication).toContainText("America/New_York");
  await expect(page.locator(".score b").first()).toHaveText("2.00");
  await expect(page.getByText("fixture unavailable", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "打开完整诊断报告", exact: true }).click();
  expect(await page.evaluate(() => window.__openedReports.at(-1))).toBe(`/api/v1/firesky/reports/${"b".repeat(64)}`);
});

test("browser cancellation remains busy until the worker reaches its real terminal state", async ({ page }) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await dialog.getByRole("button", { name: "下载缺失数据", exact: true }).click();
  await expect(dialog.getByText("fixture HTTP download", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "取消任务", exact: true }).click();
  await expect(dialog.getByText("正在取消，等待当前阶段结束", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "确认预测", exact: true })).toBeDisabled();
  expect([...state.jobs.values()].some((job) => job.method === "prepare" && job.cancelRequested)).toBe(true);
  state.cancelComplete = true;
  await expect(dialog.getByText("正在取消，等待当前阶段结束", { exact: true })).toHaveCount(0);
  expect(state.requests.some((row) => row.method === "forecast")).toBe(false);
});

test("browser settings are credential-read-only and hide local-only cache actions", async ({ page }, testInfo) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Rust 服务", exact: true })).toBeVisible();
  await expect(page.getByText("CAMS 凭据由服务端配置，浏览器不提供凭据写入。修改服务端配置后需重新启动 Rust 服务。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("ADS Personal Access Token", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存 Key", exact: true })).toHaveCount(0);
  for (const [width, height] of [[390, 667], [1440, 720]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ["light", "dark"]) {
      await page.getByLabel("主题", { exact: true }).selectOption(theme);
      await page.screenshot({ path: testInfo.outputPath(`browser-settings-${width}-${theme}.png`), fullPage: true, animations: "disabled" });
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    }
  }
  await page.getByRole("button", { name: "数据与下载", exact: true }).click();
  await expect(page.locator(".dataset")).toHaveCount(1);
  await expect(page.getByRole("button", { name: /打开 .* 缓存目录/ })).toHaveCount(0);
  expect(state.requests.some((row) => row.method === "save_cams_key")).toBe(false);
});
