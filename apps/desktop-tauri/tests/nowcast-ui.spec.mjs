import { test, expect } from "@playwright/test";

async function fixture(page) {
  await page.addInitScript(() => {
    const listeners = new Map(), callbacks = new Map(), tickets = new Map();
    const requests = [];
    let nextId = 0;
    const readyReport = {
      ready: true,
      datasets: ["gfs", "cams", "himawari"].map((source) => ({
        source, ready: true, missing_count: 0, verified: false, is_latest: false,
        request_cycle: "2026-11-11 10Z",
        latest_available_cycle: "未核验",
      })),
    };
    // A nowcast returns a report at the root, not the model_results envelope.
    const nowcastReport = {
      peak_quality_index: 0,
      quality_level: "fixture 卫星零值",
      best_time_local: "2026-11-11T18:00:00",
      timing_window: {
        start_time_local: "2026-11-11T18:00:00",
        end_time_local: "2026-11-11T18:10:00",
      },
      aerosol: { aod550: 0, source: "cams" },
      nowcast: {
        image_valid_time_utc: "2026-11-11T10:00:00Z",
        near_cloud_ratio: 0,
        far_cloud_ratio: 0.25,
        satellite_low_cloud_state: "detected",
        satellite_motion_quality: "strong",
        convective_score_ceiling: 0.5,
        geometric_path_blocking_risk: 0,
        calibrated_sample_coverage_ratio: 0.75,
        confidence: 0,
        satellite_vertical_profile: { status: "unavailable" },
      },
      steps: [
        { valid_time_local: "2026-11-11T18:00:00+08:00", instant_quality_index: 0, solar_elevation_deg: 0, quality_level: "零值", valid: true },
        { valid_time_local: "2026-11-11T18:05:00+08:00", instant_quality_index: null, solar_elevation_deg: -1, valid: true },
        { valid_time_local: "2026-11-11T18:10:00+08:00", instant_quality_index: null, solar_elevation_deg: -2, valid: false },
        { valid_time_local: "2026-11-11T18:15:00+08:00", instant_quality_index: 0, solar_elevation_deg: -3, quality_level: "零值", valid: true },
        { valid_time_local: "2026-11-11T18:20:00+08:00", instant_quality_index: 0, solar_elevation_deg: -4, quality_level: "零值", valid: true },
      ],
    };
    window.__nowcastFixture = {
      requests,
      finishForecast() {
        const ticket = [...tickets.values()].findLast((item) => item.method === "forecast");
        if (!ticket) throw new Error("No forecast was explicitly requested");
        ticket.resolve(nowcastReport);
      },
    };
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
    window.isTauri = true;
    window.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: "main" } },
      transformCallback(callback) { const id = ++nextId; callbacks.set(id, callback); return id; },
      async invoke(command, args = {}) {
        if (command === "plugin:event|listen") {
          const handlers = listeners.get(args.event) ?? [];
          handlers.push(args.handler); listeners.set(args.event, handlers);
          return args.handler;
        }
        if (command === "plugin:event|unlisten") return;
        if (command === "rpc_status") return {
          running: true, generation: 1, workspace: "/fixture", engineBinary: "/fixture/firesky-engine",
        };
        if (command === "rpc_request") {
          requests.push({ method: args.method, params: args.params });
          if (args.method === "hello") return {
            protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [],
          };
          if (args.method === "dismiss") return { dismissed: true };
        }
        if (command === "rpc_start") {
          requests.push({ method: args.method, params: args.params });
          const ticket = { method: args.method };
          ticket.result = new Promise((resolve, reject) => { ticket.resolve = resolve; ticket.reject = reject; });
          void ticket.result.catch(() => {});
          tickets.set(args.requestId, ticket);
          if (args.method === "check") ticket.resolve({ session: "nowcast-session", report: readyReport });
          return { requestId: args.requestId, generation: 1 };
        }
        if (command === "rpc_wait") return tickets.get(args.requestId).result;
        if (command === "rpc_cancel") {
          requests.push({ method: "cancel", params: args });
          tickets.get(args.requestId)?.reject({ code: "cancelled", message: "fixture cancelled" });
          return { requested: true };
        }
        throw new Error(`Unexpected nowcast fixture RPC ${command} ${args.method ?? ""}`);
      },
    };
  });
  await page.route(/https:\/\/(?:[^/]+\.)?(?:openstreetmap\.org|tianditu\.gov\.cn|arcgisonline\.com)\//, (route) => route.abort());
  await page.goto("/?mapOffline=1");
  await page.getByRole("button", { name: "实况观测", exact: true }).click();
  await expect(page.getByRole("heading", { name: "实况观测", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始实况分析", exact: true })).toBeEnabled();
}

async function analyze(page) {
  await page.getByRole("button", { name: "开始实况分析", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "实况准备", exact: true });
  await expect(dialog).toBeVisible();
  const confirm = dialog.getByRole("button", { name: "确认分析", exact: true });
  await expect(confirm).toBeDisabled();
  await expect(dialog.getByText("最新发布：未核验", { exact: true })).toHaveCount(3);
  const acknowledgement = dialog.getByLabel("最新发布尚未核验；当前周期可能不是最新，我了解后仍继续。", { exact: true });
  await acknowledgement.check();
  await expect(confirm).toBeEnabled();
  expect(await page.evaluate(() => window.__nowcastFixture.requests.filter((item) =>
    ["prepare", "forecast", "network_probe", "publications"].includes(item.method)))).toEqual([]);
  await confirm.click();
  await expect.poll(() => page.evaluate(() => window.__nowcastFixture.requests.filter((item) => item.method === "forecast").length)).toBe(1);
  await page.evaluate(() => window.__nowcastFixture.finishForecast());
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole("region", { name: "实况摘要", exact: true })).toContainText("分析完成");
}

test("nowcast uses its own explicit preparation and root report without automatically downloading or forecasting", async ({ page }) => {
  await fixture(page);
  await expect(page.locator(".model-summary")).toHaveCount(1);
  await expect(page.getByText("卫星实况", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.__nowcastFixture.requests.filter((item) =>
    ["check", "prepare", "forecast", "network_probe", "publications"].includes(item.method)))).toEqual([]);
  await analyze(page);
  const calls = await page.evaluate(() => window.__nowcastFixture.requests);
  const check = calls.find((item) => item.method === "check");
  expect(check.params.kind).toBe("nowcast");
  expect(check.params.payload).toMatchObject({
    name: "杭州", lat: 30.2741, lon: 120.1551, timezone_name: "Asia/Shanghai",
    model_source: "both", workers: 1,
  });
  expect(calls.filter((item) => item.method === "forecast").map((item) => item.params)).toEqual([
    { session: "nowcast-session", confirmed: true, cycle_age_confirmed: true },
  ]);
  expect(calls.some((item) => item.method === "prepare")).toBe(false);
  await expect(page.locator(".model-summary .score b")).toHaveText("0.00");
  const metric = (title) => page.locator(".metric").filter({ has: page.getByRole("heading", { name: title, exact: true }) });
  await expect(metric("近场云量").locator("strong")).toHaveText("0%");
  await expect(metric("远场云量").locator("strong")).toHaveText("25%");
  await expect(metric("低云状态").locator("strong")).toHaveText("检出低云");
  await expect(metric("运动质量").locator("strong")).toHaveText("可靠");
  await expect(metric("对流顶棚").locator("strong")).toHaveText("50%");
  await expect(metric("光路阻塞风险").locator("strong")).toHaveText("0%");
  await expect(metric("采样覆盖率").locator("strong")).toHaveText("75%");
  await expect(metric("实况置信度").locator("strong")).toHaveText("0%");
  await expect(metric("AOD 550").locator("strong")).toHaveText("0.00");
  const curve = page.getByRole("img", { name: "质量指数时间曲线", exact: true });
  await expect(curve).toBeVisible();
  await expect(curve.locator("polyline")).toHaveCount(2);
  await expect(curve.locator("circle")).toHaveCount(3);
  expect(await curve.locator("polyline").evaluateAll((lines) =>
    lines.map((line) => line.getAttribute("points").split(" ").length))).toEqual([1, 2]);
  expect(await curve.locator("circle").evaluateAll((points) =>
    points.map((point) => point.getAttribute("cy")))).toEqual(["160", "160", "160"]);
});

test("nowcast ledger distinguishes valid zero, absent readings and missing samples; unavailable profiles stay empty", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await analyze(page);
  await page.getByRole("tab", { name: "逐时台账", exact: true }).click();
  const rows = page.getByRole("tabpanel", { name: "逐时台账", exact: true }).locator("tbody tr");
  await expect(rows).toHaveCount(5);
  await expect(rows.nth(0).locator("td").nth(1)).toHaveText("0.000");
  await expect(rows.nth(0).locator("td").nth(4)).toHaveText("有效");
  await expect(rows.nth(1).locator("td").nth(1)).toHaveText("--");
  await expect(rows.nth(1).locator("td").nth(4)).toHaveText("无数据");
  await expect(rows.nth(2).locator("td").nth(1)).toHaveText("--");
  await expect(rows.nth(2).locator("td").nth(4)).toHaveText("缺测");
  await page.getByRole("tab", { name: "大气剖面", exact: true }).click();
  const profile = page.getByRole("tabpanel", { name: "大气剖面", exact: true });
  await expect(profile.getByRole("heading", { name: "太阳方向卫星剖面", exact: true })).toBeVisible();
  await expect(profile.getByText("暂无可用卫星剖面采样", { exact: true })).toBeVisible();
  await expect(profile.locator("pre")).toHaveCount(0);
  expect(errors).toEqual([]);
});
