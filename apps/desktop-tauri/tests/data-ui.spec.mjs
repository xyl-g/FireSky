import { test, expect } from "@playwright/test";

// Isolated native-transport fixture; browser HTTP coverage lives in browser-http-ui.
async function fixture(page) {
  await page.addInitScript(() => {
    const listeners = new Map();
    const callbacks = new Map();
    const tickets = new Map();
    const requests = [];
    const historyTasks = [
      {
        task_id: "a".repeat(64), request_id: "fixture-interrupted", method: "forecast", status: "interrupted",
        created_at_utc: "2026-11-11T08:00:00Z", updated_at_utc: "2026-11-11T08:01:00Z",
        cancel_requested: false, event_seq: 2, progress: { stage: "assessment", percent: 0 },
        retry_request: { kind: "point", payload: {
          name: "历史地点", lat: 30.2741, lon: 120.1551, elevation_m: 8, timezone_name: "Asia/Shanghai",
          target_date: "2026-11-15", phenomenon: "dusk", model_source: "both", forecast_mode: "model",
          satellite_source: "off", ecmwf_source: "ecmwf",
        } },
        events: [
          { seq: 1, at_utc: "2026-11-11T08:00:00Z", event: "running", data: { method: "forecast" } },
          { seq: 2, at_utc: "2026-11-11T08:01:00Z", event: "interrupted", data: { reason: "runtime_exited", retry_requires_check: true } },
        ],
      },
    ];
    const activeTicket = (method) => [...tickets.values()].findLast((item) => item.method === method && !item.settled);
    let nextId = 0;
    let generation = 1;
    const report = (ready) => ({
      ready, datasets: ["gfs", "ecmwf", "cams"].map((source) => ({
        source, ready, request_cycle: "2026-11-11 00Z",
        missing_count: ready ? 0 : 2, verified: false,
      })),
    });
    const emit = (name, payload) => {
      for (const handler of listeners.get(name) ?? []) callbacks.get(handler)?.({ payload });
    };
    const datasets = ["gfs", "ecmwf", "cams"].map((source) => ({
      source, path: `/fixture/cache/${source}/long-but-valid-cache-directory`,
      total_file_count: source === "cams" ? 0 : 2, total_storage_mb: source === "cams" ? 0 : 2048,
      unindexed_file_count: 0, latest_cycle: source === "cams" ? null : "2026-11-11 00Z",
      cycles: source === "cams" ? {} : {
        "2026111100": { label: "2026-11-11 00Z", files: 2, mb: 2048, hours: [0, 3, 6, 9, 12, 15, 18, 21, 24], coverage: { 0: [[0, 125, 40, 0]] } },
      },
    }));
    window.__rpcFixture = {
      requests,
      finishPrepare(ready = true) {
        const ticket = activeTicket("prepare");
        ticket.resolve({ session: "fixture-session", report: report(ready) });
      },
      failPrepare() {
        const ticket = activeTicket("prepare");
        ticket.reject({ code: "operation_failed", message: "fixture transfer failed" });
      },
      finishCancel() {
        for (const ticket of tickets.values()) {
          if (ticket.cancelled) ticket.reject({ code: "cancelled", message: "fixture cancelled" });
        }
      },
      disconnect() { generation++; emit("engine-status", { running: false, generation, spawnError: "fixture disconnected" }); },
      finishProbe() {
        const ticket = activeTicket("network_probe");
        ticket.resolve({ checked_at: "2026-11-11T00:00:00Z", datasets: [
          { source: "gfs", state: "passed", detail: "fixture decoded GRIB", bytes_received: 1024, elapsed_ms: 200 },
          { source: "ec", state: "timeout", detail: "fixture timed out" },
          { source: "cams", state: "not_configured", detail: "fixture missing credentials" },
        ] });
      },
      appendHistoryEvent() {
        const item = historyTasks[0];
        item.status = "done";
        item.event_seq += 1;
        item.updated_at_utc = "2026-11-11T08:02:00Z";
        item.events.push({ seq: item.event_seq, at_utc: item.updated_at_utc, event: "done", data: {} });
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
          handlers.push(args.handler); listeners.set(args.event, handlers); return args.handler;
        }
        if (command === "plugin:event|unlisten") return;
        if (command === "rpc_status" || command === "rpc_restart") return { running: true, generation };
        if (command === "rpc_request") {
          requests.push({ method: args.method, params: args.params });
          if (args.method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [
            { code: "east-asia", label: "东亚" }, { code: "china", label: "中国" },
          ] };
          if (args.method === "inventory") return { datasets };
          if (args.method === "cams_credentials") return { configured: false, can_save: true, description: "fixture CAMS not configured" };
          if (args.method === "dismiss") return { dismissed: true };
          if (args.method === "publications") return { session: "fixture-session", report: report(false) };
          if (args.method === "task_history") return {
            schema: "firesky-task-history-v1", tasks: historyTasks.map(({ events, ...item }) => item),
            total: historyTasks.length, issues: [], automatic_restart: false,
          };
          if (args.method === "task_events") {
            const item = historyTasks.find((row) => row.task_id === args.params.task_id);
            return { task_id: args.params.task_id, status: item?.status, events: item?.events ?? [], next_seq: item?.event_seq ?? 0, truncated: false };
          }
          if (args.method === "task_forget") return { forgotten: true };
        }
        if (command === "rpc_start") {
          requests.push({ method: args.method, params: args.params });
          const ticket = { method: args.method, settled: false };
          ticket.result = new Promise((resolve, reject) => {
            ticket.resolve = (value) => { ticket.settled = true; resolve(value); };
            ticket.reject = (error) => { ticket.settled = true; reject(error); };
          });
          void ticket.result.catch(() => {});
          tickets.set(args.requestId, ticket);
          if (args.method === "check") ticket.resolve({ session: "fixture-session", report: report(false) });
          if (args.method === "prepare") queueMicrotask(() => emit("engine-progress", {
            requestId: args.requestId, event: { percent: 49, message: "GFS fixture preparation" },
          }));
          return { requestId: args.requestId, generation };
        }
        if (command === "rpc_wait") return tickets.get(args.requestId).result;
        if (command === "rpc_cancel") {
          requests.push({ method: "cancel", params: args });
          tickets.get(args.requestId).cancelled = true;
          return { requested: true };
        }
        throw new Error(`Unexpected fixture command ${command}`);
      },
    };
  });
  await page.goto("/?mapOffline=1");
  await expect(page.locator(".engine-badge.ok")).toContainText("引擎已连接");
  await page.getByRole("button", { name: "数据与下载", exact: true }).click();
  await expect(page.getByText("0.0 MB", { exact: true })).toBeVisible();
}

test("data preparation uses an explicit dialog, retains task on navigation, and never forecasts", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "检查数据", exact: true }).click();
  await expect(page.getByText("本次数据有缺失", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "准备 / 更新数据" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Control+1");
  await expect(page.getByRole("heading", { name: "数据与下载", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "确认准备", exact: true }).click();
  await expect(page.getByText("GFS fixture preparation · 49%")).toBeVisible();
  await page.getByRole("button", { name: "单点预测", exact: true }).click();
  await page.getByRole("button", { name: "数据与下载", exact: true }).click();
  await expect(page.getByRole("button", { name: "取消", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByRole("button", { name: "取消中…", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "检查数据", exact: true })).toBeDisabled();
  await page.evaluate(() => window.__rpcFixture.finishCancel());
  await expect(page.getByRole("button", { name: "检查数据", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "检查数据", exact: true }).click();
  await expect(page.getByText("本次数据有缺失", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "准备 / 更新数据" }).click();
  await page.getByRole("button", { name: "确认准备", exact: true }).click();
  await page.evaluate(() => window.__rpcFixture.finishPrepare());
  await expect(page.getByText("本次数据已齐", { exact: true })).toBeVisible();
  const methods = await page.evaluate(() => window.__rpcFixture.requests.map((request) => request.method));
  expect(methods).not.toContain("forecast");
  expect(methods).not.toContain("network_probe");
  expect(methods).not.toContain("publications");
  await page.getByRole("combobox", { name: "事件", exact: true }).selectOption("dawn");
  await expect(page.getByText("尚未检查", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "准备 / 更新数据" })).toBeDisabled();
});

test("probe failures stay distinct from decoded success and reconnect invalidates readiness", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "探测下载", exact: true }).click();
  await page.evaluate(() => window.__rpcFixture.finishProbe());
  await expect(page.getByText("下载解码通过", { exact: true })).toBeVisible();
  await expect(page.getByText("超时", { exact: true })).toBeVisible();
  await expect(page.getByText("未配置凭据", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "检查数据", exact: true }).click();
  await expect(page.getByText("本次数据有缺失", { exact: true })).toBeVisible();
  await page.evaluate(() => window.__rpcFixture.disconnect());
  await expect(page.getByRole("button", { name: "准备 / 更新数据" })).toBeDisabled();
  await expect(page.getByText("尚未检查", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.getByRole("button", { name: "检查数据", exact: true })).toBeEnabled();
});

test("populated data layout fits narrow and wide windows in both themes", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await page.getByRole("button", { name: "检查数据", exact: true }).click();
  for (const [width, height] of [[390, 667], [768, 720], [1024, 720], [1440, 720]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ["light", "dark"]) {
      await expect(page.getByRole("button", { name: "检查数据", exact: true })).toBeVisible();
      await page.evaluate(async (value) => {
        document.documentElement.dataset.theme = value;
        document.querySelector(".workspace").scrollTop = 0;
        await document.fonts.ready;
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }, theme);
      const overflow = await page.evaluate(() => ({
        document: document.documentElement.scrollWidth > innerWidth,
        workspace: document.querySelector(".workspace").scrollWidth > document.querySelector(".workspace").clientWidth,
      }));
      expect(overflow).toEqual({ document: false, workspace: false });
      await page.screenshot({ path: testInfo.outputPath(`data-${width}-${theme}.png`), fullPage: true, animations: "disabled", caret: "hide" });
    }
  }
  expect(errors).toEqual([]);
});

test("changing EC source does not mislabel an in-flight probe or retain the old check snapshot", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "检查数据", exact: true }).click();
  await expect(page.getByText("本次数据有缺失", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "探测下载", exact: true }).click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByLabel("EC 下载源", { exact: true }).selectOption("aws");
  await page.getByRole("button", { name: "数据与下载", exact: true }).click();
  await expect(page.getByRole("button", { name: "取消探测", exact: true })).toBeVisible();
  await expect(page.getByText("尚未检查", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "准备 / 更新数据" })).toBeDisabled();
  expect(await page.evaluate(() => window.__rpcFixture.requests.filter((request) => request.method === "cancel"))).toHaveLength(0);
  await page.evaluate(() => window.__rpcFixture.finishProbe());
  await expect(page.getByText("EC 下载源已变更，请重新探测当前来源。", { exact: true })).toBeVisible();
  await expect(page.getByText("下载解码通过", { exact: true })).toHaveCount(0);
  await expect(page.getByText("未探测", { exact: true })).toHaveCount(3);
  await page.getByRole("button", { name: "检查数据", exact: true }).click();
  await expect(page.getByText("本次数据有缺失", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "探测下载", exact: true }).click();
  expect(await page.evaluate(() => ({
    probe: window.__rpcFixture.requests.findLast((request) => request.method === "network_probe").params.ecmwf_source,
    check: window.__rpcFixture.requests.findLast((request) => request.method === "check").params.payload.ecmwf_source,
  }))).toEqual({ probe: "aws", check: "aws" });
  await page.evaluate(() => window.__rpcFixture.finishProbe());
  await expect(page.getByText("下载解码通过", { exact: true })).toBeVisible();
});

test("task history exposes interrupted work, replays terminal events, and restores without restarting", async ({ page }, testInfo) => {
  await fixture(page);
  await expect(page.getByText("未自动重跑", { exact: true })).toBeVisible();
  const row = page.locator(".task-row").filter({ hasText: "历史地点" });
  await row.getByRole("button", { name: "查看预测计算事件", exact: true }).click();
  await expect(page.getByRole("heading", { name: "预测计算事件", exact: true })).toBeVisible();
  await expect(page.getByText("已中断", { exact: true }).first()).toBeVisible();
  await page.evaluate(() => window.__rpcFixture.appendHistoryEvent());
  await page.getByRole("button", { name: "刷新任务历史", exact: true }).click();
  await expect(page.locator(".events ol").getByText("完成", { exact: true })).toBeVisible();
  const history = page.locator(".history-section");
  for (const [width, height] of [[390, 667], [1440, 720]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ["light", "dark"]) {
      await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
      await history.scrollIntoViewIfNeeded();
      const layout = await page.locator(".task-row").evaluateAll((rows) => rows.map((row) => {
        const main = row.querySelector(".task-main").getBoundingClientRect();
        const actions = row.querySelector(".actions").getBoundingClientRect();
        return { overlap: main.right > actions.left + 1 && actions.right > main.left + 1 && main.bottom > actions.top + 1 && actions.bottom > main.top + 1 };
      }));
      expect(layout.some((item) => item.overlap)).toBe(false);
      expect(await history.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(false);
      await history.screenshot({ path: testInfo.outputPath(`history-${width}-${theme}.png`), animations: "disabled" });
    }
  }

  await row.getByRole("button", { name: "恢复参数", exact: true }).click();
  await expect(page.getByRole("heading", { name: "单点预测", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始预测", exact: true })).toBeEnabled();
  let methods = await page.evaluate(() => window.__rpcFixture.requests.map((request) => request.method));
  expect(methods).not.toContain("check");
  expect(methods).not.toContain("prepare");
  expect(methods).not.toContain("forecast");

  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await expect(dialog).toContainText("历史地点 · 2026-11-15 · 晚霞");
  methods = await page.evaluate(() => window.__rpcFixture.requests.map((request) => request.method));
  expect(methods).toContain("check");
  expect(methods).not.toContain("prepare");
  expect(methods).not.toContain("forecast");
});
