import { test, expect } from "@playwright/test";
import { IN_PAGE_RPC_MOCK } from "./helpers/in-page-rpc.mjs";

async function fixture(page) {
  await page.addInitScript(IN_PAGE_RPC_MOCK);
  await page.addInitScript(() => {
    const requests = [];
    window.__regionDrawing = { requests };
    localStorage.setItem("firesky.appearance", "light");
    localStorage.setItem("firesky.mapProvider", "tianditu");
    window.__rpcStatus = () => ({ generation: 1 });
    window.__rpcDirect = (method) => {
      if (method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [{ code: "east-asia", label: "东亚" }] };
      if (method === "cams_credentials") return { configured: false, can_save: true };
      if (method === "dismiss") return { dismissed: true };
      // 未声明方法返回 undefined：交给 __rpcSubmit 记录完整 params。
      return undefined;
    };
    window.__rpcSubmit = (job) => {
      requests.push({ method: job.method, params: job.params });
      if (job.method === "check") job.resolve({ session: "draw-fixture", report: { ready: true, datasets: [] } });
    };
    window.__rpcCancelJob = () => false;
  });
  await page.route(/https:\/\/(?:[^/]+\.)?(?:tianditu\.gov\.cn|openfreemap\.org)\//, (route) => route.abort());
  await page.goto("/?mapOffline=1");
  await expect(page.locator(".engine-badge.ok")).toContainText("引擎已连接");
  await page.getByRole("button", { name: "区域预测", exact: true }).click();
  await expect(page.locator(".model-map .fs-map-root")).toHaveAttribute("data-map-state", "ready");
}

const mapRoot = (page) => page.locator(".model-map .fs-map-root").first();
const mapSurface = (page) => page.locator(".model-map .leaflet-container").first();

async function drag(page, surface, touch = false) {
  const box = await surface.boundingBox();
  const start = { x: box.x + box.width * .32, y: box.y + box.height * .3 };
  const end = { x: box.x + box.width * .7, y: box.y + box.height * .65 };
  if (touch) {
    const client = await page.context().newCDPSession(page);
    await client.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [start] });
    await client.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [end] });
    await client.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await client.detach();
  } else {
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(end.x, end.y, { steps: 8 });
    await page.mouse.up();
  }
}

test("regional rectangle drawing writes snapped custom bounds without starting a forecast", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await page.getByRole("button", { name: "绘制范围", exact: true }).click();
  await expect(mapRoot(page)).toHaveAttribute("data-drawing", "true");
  await expect(mapSurface(page)).toHaveCSS("cursor", "crosshair");
  await drag(page, mapSurface(page));
  await expect(mapRoot(page)).toHaveAttribute("data-drawing", "false");
  await expect(page.locator(".firesky-region-selection")).toHaveCount(1);
  await expect(page.getByRole("checkbox", { name: "启用自定义范围", exact: true })).toBeChecked();
  const bounds = {};
  for (const [key, label] of Object.entries({ west: "西界", east: "东界", south: "南界", north: "北界" })) {
    bounds[key] = Number(await page.getByLabel(label, { exact: true }).inputValue());
    expect(bounds[key] * 4).toBe(Math.round(bounds[key] * 4));
  }
  expect(bounds.west).toBeLessThan(bounds.east);
  expect(bounds.south).toBeLessThan(bounds.north);
  expect(await page.evaluate(() => window.__regionDrawing.requests.filter((call) => ["check", "prepare", "forecast"].includes(call.method)))).toEqual([]);
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "预测准备" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__regionDrawing.requests.some((call) => call.method === "check"))).toBe(true);
  const check = await page.evaluate(() => window.__regionDrawing.requests.find((call) => call.method === "check"));
  expect(check.params.payload.custom_bounds).toEqual(bounds);
  expect(check.params.payload.regions).toEqual(["east-asia", "custom"]);
  await page.getByRole("dialog", { name: "预测准备" }).getByRole("button", { name: "返回", exact: true }).click();
  await page.getByRole("button", { name: "清除绘制范围", exact: true }).click();
  await expect(page.locator(".firesky-region-selection")).toHaveCount(0);
  await expect(page.getByRole("checkbox", { name: "启用自定义范围", exact: true })).not.toBeChecked();
  await page.screenshot({ path: testInfo.outputPath("regional-drawing-cleared.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("Escape cancels rectangle drawing and restores normal map panning", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "绘制范围", exact: true }).click();
  await expect(mapSurface(page)).toHaveCSS("cursor", "crosshair");
  await page.keyboard.press("Escape");
  await expect(mapRoot(page)).toHaveAttribute("data-drawing", "false");
  await expect(page.getByRole("button", { name: "绘制范围", exact: true })).toHaveAttribute("aria-pressed", "false");
  const pane = mapSurface(page).locator(".leaflet-map-pane");
  const previous = await pane.getAttribute("style");
  await drag(page, mapSurface(page));
  await expect(pane).not.toHaveAttribute("style", previous);
  await expect(page.locator(".firesky-region-selection")).toHaveCount(0);
});

test("touch rectangle drawing keeps the compact toolbar within the phone width", async ({ browser }, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  try {
    await fixture(page);
    await page.getByRole("button", { name: "绘制范围", exact: true }).click();
    await expect(mapSurface(page)).toHaveCSS("cursor", "crosshair");
    await drag(page, mapSurface(page), true);
    await expect(page.locator(".firesky-region-selection")).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("regional-drawing-phone.png"), fullPage: true });
  } finally { await context.close(); }
});
