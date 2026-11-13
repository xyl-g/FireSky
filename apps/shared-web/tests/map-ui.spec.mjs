import { test, expect } from "@playwright/test";
import { IN_PAGE_RPC_MOCK } from "./helpers/in-page-rpc.mjs";

const KEY = "fixture-browser-key-not-real";
const mapDialog = (page) => page.getByRole("dialog", { name: "地图选点", exact: true });

async function fixture(page, query = "mapOffline=1&mapDebug=1") {
  const retiredRequests = [];
  const openFreeRequests = [];
  const logs = [];
  page.on("request", (request) => {
    const url = request.url();
    if (/apple-mapkit|mapkit\.js|openstreetmap\.org|arcgisonline\.com/i.test(url)) retiredRequests.push(url);
    if (/openfreemap\.org/i.test(url)) openFreeRequests.push(url);
  });
  page.on("console", (message) => logs.push(message.text()));
  page.on("pageerror", (error) => logs.push(error.message));
  await page.addInitScript(IN_PAGE_RPC_MOCK);
  await page.addInitScript(() => {
    localStorage.setItem("firesky.appearance", "light");
    if (!sessionStorage.getItem("firesky.mapFixture.initialized")) {
      localStorage.removeItem("firesky.mapProvider");
      localStorage.removeItem("firesky.mapBase");
      sessionStorage.setItem("firesky.mapFixture.initialized", "1");
    }
    window.__rpcStatus = () => ({ generation: 1 });
    window.__rpcDirect = (method, params) => {
      if (method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [] };
      if (method === "cams_credentials") return { configured: false, can_save: true };
      if (method === "metadata") return { lat: params.lat, lon: params.lon, timezone_name: "Asia/Shanghai", elevation_m: 0 };
      throw new Error("Unexpected fixture RPC");
    };
    window.__rpcSubmit = () => {};
  });
  await page.route("https://cdn.apple-mapkit.com/**", (route) => route.abort());
  await page.route(/https:\/\/(?:[^/]+\.)?(?:openstreetmap\.org|tianditu\.gov\.cn|arcgisonline\.com|openfreemap\.org)\//, (route) => route.abort());
  await page.goto(`/?${query}`);
  await expect(page.getByRole("heading", { name: "单点预测", exact: true })).toBeVisible();
  await expect(page.getByLabel("地图服务")).toHaveCount(0);
  return { retiredRequests, openFreeRequests, logs };
}

async function settings(page) {
  const dialog = mapDialog(page);
  if (await dialog.isVisible()) await dialog.getByRole("button", { name: "完成", exact: true }).click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByLabel("地图服务")).toBeVisible();
}

async function mapPage(page) {
  if (!await mapDialog(page).isVisible()) {
    await page.getByRole("button", { name: "单点预测", exact: true }).click();
    await page.getByRole("button", { name: "大地图", exact: true }).click();
  }
  await expect(mapDialog(page)).toBeVisible();
  await expect(page.getByLabel("地图服务")).toHaveCount(0);
  await expect(mapDialog(page).locator(".fs-layers")).toHaveCount(0);
  await expect(mapDialog(page).getByRole("group", { name: "地图源", exact: true })).toHaveCount(0);
  await expect(mapDialog(page).getByRole("group", { name: "底图类型", exact: true })).toHaveCount(0);
}

async function provider(page, value) {
  await settings(page);
  await page.getByLabel("地图服务").selectOption(value);
  await mapPage(page);
  await expect(mapDialog(page).locator(".fs-map-root")).toHaveAttribute("data-map-state", "ready");
}

async function openFreeStyle(page) {
  await page.route(/https:\/\/tiles\.openfreemap\.org\/styles\/(?:liberty|dark)(?:\?.*)?$/, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      version: 8, sources: {},
      layers: [{ id: "fixture-background", type: "background", paint: { "background-color": "#326c93" } }],
    }),
  }));
}

async function loadGrid(page) {
  const tools = mapDialog(page).locator(".grid-tools");
  await tools.locator("summary").click();
  await tools.getByRole("button", { name: "加载三格", exact: true }).click();
  await expect(mapDialog(page).locator(".fs-map-root")).toHaveAttribute("data-grid-count", "3");
  await expect(tools.getByRole("button", { name: /^聚焦 / })).toHaveText([
    "聚焦 1 · 0.72", "聚焦 2 · 0", "聚焦 3 · 缺测",
  ]);
}

async function expectPopup(page, text) {
  const popup = mapDialog(page).locator(".leaflet-popup-content");
  await expect(popup).toHaveCount(1);
  await expect(popup).toContainText(text);
}

async function expectGridPixels(page) {
  const canvas = mapDialog(page).locator("canvas.firesky-forecast-grid");
  await expect(canvas).toHaveCount(1);
  await expect.poll(() => canvas.evaluate((element) => {
    if (!element.width || !element.height) return false;
    const pixels = element.getContext("2d").getImageData(0, 0, element.width, element.height).data;
    for (let index = 3; index < pixels.length; index += 4) if (pixels[index] > 0) return true;
    return false;
  })).toBe(true);
}

async function expectWebGLPixels(page) {
  const canvas = mapDialog(page).locator("canvas.maplibregl-canvas");
  await expect(canvas).toBeVisible();
  const context = await canvas.evaluate((element) => {
    const gl = element.getContext("webgl2") || element.getContext("webgl");
    return gl ? { version: gl.getParameter(gl.VERSION), width: gl.drawingBufferWidth, height: gl.drawingBufferHeight } : null;
  });
  expect(context).not.toBeNull();
  expect(context.version).toContain("WebGL");
  expect(context.width).toBeGreaterThan(100);
  expect(context.height).toBeGreaterThan(100);
  // Screenshot pixels remain readable when WebGL's drawing buffer is discarded.
  await expect.poll(async () => {
    const image = (await canvas.screenshot()).toString("base64");
    return page.evaluate(async (png) => {
      const bytes = Uint8Array.from(atob(png), (character) => character.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
      const surface = document.createElement("canvas");
      surface.width = bitmap.width; surface.height = bitmap.height;
      const ctx = surface.getContext("2d");
      ctx.drawImage(bitmap, 0, 0);
      const pixel = [...ctx.getImageData(Math.floor(surface.width * .85), Math.floor(surface.height * .15), 1, 1).data];
      bitmap.close();
      return pixel[3] === 255 ? Math.max(...pixel.slice(0, 3)) - Math.min(...pixel.slice(0, 3)) : 0;
    }, image);
  }).toBeGreaterThan(20);
}

test("TianDiTu is default and Settings offers only TianDiTu and real WebGL OpenFreeMap", async ({ page }, testInfo) => {
  const { retiredRequests, openFreeRequests } = await fixture(page);
  await settings(page);
  await expect(page.getByLabel("地图服务")).toHaveValue("tianditu");
  await expect(page.getByLabel("地图服务").locator("option")).toHaveText([
    "天地图", "OpenFreeMap",
  ]);
  await expect(page.getByLabel(/Apple Maps Token/)).toHaveCount(0);
  await expect(page.getByLabel("苹果地图类型")).toHaveCount(0);
  await expect(page.getByLabel("底图类型").locator("option")).toHaveText(["影像", "街道", "地形"]);
  await page.getByLabel("底图类型").selectOption("terrain");
  await mapPage(page);
  await expect(mapDialog(page).locator(".fs-map-root")).toHaveAttribute("data-map-state", "ready");
  await expect(mapDialog(page).locator(".leaflet-container")).toBeVisible();
  await expect(mapDialog(page).locator(".fs-key")).toHaveCount(0);
  await provider(page, "openfreemap");
  await expect(mapDialog(page).locator(".maplibregl-canvas")).toHaveCount(0);
  await expect(mapDialog(page).locator(".fs-key")).toHaveCount(0);
  await page.reload();
  await settings(page);
  await expect(page.getByLabel("地图服务")).toHaveValue("openfreemap");
  await expect(page.getByLabel("底图类型")).toHaveCount(0);
  await page.getByLabel("地图服务").selectOption("tianditu");
  await expect(page.getByLabel("底图类型")).toHaveValue("terrain");
  await mapPage(page);
  await expect(mapDialog(page).locator(".leaflet-container")).toBeVisible();
  expect(retiredRequests).toEqual([]);
  expect(openFreeRequests).toEqual([]);
  await openFreeStyle(page);
  await page.goto("/?mapDebug=1");
  await provider(page, "openfreemap");
  await expectWebGLPixels(page);
  await expect(mapDialog(page).locator(".fs-key")).toHaveCount(0);
  await loadGrid(page);
  await expectGridPixels(page);
  await mapDialog(page).getByRole("button", { name: "聚焦 2 · 0", exact: true }).click();
  await expectPopup(page, "峰值 0.000");
  await mapDialog(page).getByRole("button", { name: "清除网格", exact: true }).click();
  await expect(mapDialog(page).locator(".leaflet-popup-content")).toHaveCount(0);
  const previous = await mapDialog(page).locator(".coordinates").textContent();
  const surface = mapDialog(page).locator(".leaflet-container");
  const box = await surface.boundingBox();
  await surface.click({ position: { x: box.width * .35, y: box.height * .45 } });
  await expect(mapDialog(page).locator(".coordinates")).not.toHaveText(previous);
  expect(openFreeRequests.length).toBeGreaterThan(0);
  expect(retiredRequests).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("openfreemap-real-webgl-empty-style.png"), fullPage: true });
});

test("OpenFreeMap WebGL failure is recoverable through Settings without page errors", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, ...args) {
      if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") return null;
      return original.call(this, type, ...args);
    };
  });
  await fixture(page, "mapDebug=1");
  await provider(page, "openfreemap");
  await expect(mapDialog(page).locator(".fs-health [role=alert]")).toBeVisible();
  await expect(mapDialog(page).locator(".fs-health")).toContainText(/OpenFreeMap|WebGL|初始化失败/);
  await settings(page);
  await page.getByLabel("地图服务").selectOption("tianditu");
  await mapPage(page);
  await expect(mapDialog(page).locator(".fs-map-root")).toHaveAttribute("data-map-state", "ready");
  await expect(mapDialog(page).locator(".fs-health [role=alert]")).toHaveCount(0);
  await page.waitForTimeout(300);
  expect(pageErrors).toEqual([]);
});

test("normal UI hides debug tools and READY while mini maps never expose debug tools", async ({ page }) => {
  await fixture(page, "mapOffline=1");
  const mini = page.locator(".mini-map");
  await expect(mini.locator(".fs-map-root")).toHaveAttribute("data-map-state", "ready");
  await expect(mini.locator(".grid-tools, .fs-fixture")).toHaveCount(0);
  await expect(mini.locator(".fs-health")).toBeHidden();
  await mapPage(page);
  await expect(mapDialog(page).locator(".grid-tools, .fs-fixture")).toHaveCount(0);
  await expect(mapDialog(page).locator(".fs-health")).toBeHidden();
  await page.goto("/?mapOffline=1&mapDebug=1");
  await expect(mini.locator(".fs-map-root")).toHaveAttribute("data-map-state", "ready");
  await expect(mini.locator(".grid-tools, .fs-fixture")).toHaveCount(0);
  await expect(mini.locator(".fs-health")).toBeHidden();
  await mapPage(page);
  await expect(mapDialog(page).locator(".grid-tools")).toBeVisible();
  await expect(mapDialog(page).locator(".fs-fixture")).toBeVisible();
  await provider(page, "openfreemap");
  await expect(page.locator(".mini-map .grid-tools, .mini-map .fs-fixture")).toHaveCount(0);
  await expect(page.locator(".mini-map .fs-health")).toBeHidden();
  await expect(mapDialog(page).locator(".grid-tools")).toBeVisible();
  await expect(mapDialog(page).locator(".fs-health")).toBeVisible();
  await expect(mapDialog(page).locator(".maplibregl-canvas")).toHaveCount(0);
});

test("missing default TianDiTu key gives Settings guidance without a silent provider fallback", async ({ page }) => {
  const { retiredRequests, openFreeRequests } = await fixture(page, "");
  await mapPage(page);
  const dialog = mapDialog(page);
  await expect(dialog.getByRole("heading", { name: "需要配置天地图 Key", exact: true })).toBeVisible();
  await expect(dialog.getByText("请在设置中配置天地图浏览器 Key，或切换到 OpenFreeMap。", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: /^使用 / })).toHaveCount(0);
  await expect(dialog.locator(".maplibregl-canvas")).toHaveCount(0);
  await expect(dialog.locator(".grid-tools, .fs-fixture")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: /开发测试/ })).toHaveCount(0);
  await settings(page);
  await expect(page.getByLabel("地图服务")).toHaveValue("tianditu");
  await mapPage(page);
  await expect(dialog.getByRole("heading", { name: "需要配置天地图 Key", exact: true })).toBeVisible();
  expect(openFreeRequests).toEqual([]);
  expect(retiredRequests).toEqual([]);
});

test("TianDiTu key is masked and cleared after Apply without DOM, log or storage exposure", async ({ page }) => {
  const { retiredRequests, logs } = await fixture(page);
  await settings(page);
  await page.getByLabel("地图服务").selectOption("tianditu");
  const input = page.getByLabel(/^天地图浏览器 Key/);
  await expect(input).toHaveAttribute("type", "password");
  await input.fill(KEY);
  await page.getByRole("button", { name: "应用地图凭据", exact: true }).click();
  await expect(input).toHaveValue("");
  await expect(page.getByRole("button", { name: "应用地图凭据", exact: true })).toBeDisabled();
  const exposure = () => page.evaluate((key) => ({
    attributes: [...document.querySelectorAll("*")].some((element) =>
      [...element.attributes].some((attribute) => attribute.value.includes(key))),
    html: document.documentElement.outerHTML.includes(key),
    local: JSON.stringify(localStorage).includes(key),
    session: JSON.stringify(sessionStorage).includes(key),
    inputs: [...document.querySelectorAll("input")].some((element) => element.value.includes(key)),
  }), KEY);
  const hidden = { attributes: false, html: false, local: false, session: false, inputs: false };
  expect(await exposure()).toEqual(hidden);
  await mapPage(page);
  await expect(mapDialog(page).locator(".fs-key")).toHaveCount(0);
  expect(await exposure()).toEqual(hidden);
  await settings(page);
  await expect(page.getByLabel(/^天地图浏览器 Key/)).toHaveValue("");
  await expect(page.getByLabel(/^天地图浏览器 Key/)).toHaveAttribute("placeholder", "输入以替换");
  await page.goto("/?mapDebug=1");
  await mapPage(page);
  await expect(mapDialog(page).getByRole("heading", { name: "需要配置天地图 Key", exact: true })).toBeVisible();
  await settings(page);
  await expect(page.getByLabel(/^天地图浏览器 Key/)).toHaveAttribute("placeholder", "浏览器端 Key");
  expect(await exposure()).toEqual(hidden);
  expect(logs.some((line) => line.includes(KEY))).toBe(false);
  expect(retiredRequests).toEqual([]);
});

test("a map click updates the shared observation point and survives provider round trips", async ({ page }) => {
  await fixture(page);
  await mapPage(page);
  const dialog = mapDialog(page);
  const previous = await dialog.locator(".coordinates").textContent();
  const surface = dialog.locator(".leaflet-container");
  const box = await surface.boundingBox();
  expect(box).not.toBeNull();
  await surface.click({ position: { x: box.width * .35, y: box.height * .45 } });
  await expect(dialog.locator(".coordinates")).not.toHaveText(previous);
  let coordinates = await dialog.locator(".coordinates").textContent();
  await provider(page, "openfreemap");
  await expect(dialog.locator(".coordinates")).toHaveText(coordinates);
  const vectorSurface = dialog.locator(".leaflet-container");
  const vectorBox = await vectorSurface.boundingBox();
  await vectorSurface.click({ position: { x: vectorBox.width * .65, y: vectorBox.height * .4 } });
  await expect(dialog.locator(".coordinates")).not.toHaveText(coordinates);
  coordinates = await dialog.locator(".coordinates").textContent();
  const [lat, lon] = coordinates.match(/-?\d+\.\d+/g).map(Number);
  for (const value of ["tianditu", "openfreemap", "tianditu"]) {
    await provider(page, value);
    await expect(dialog.locator(".coordinates")).toHaveText(coordinates);
    await expect(dialog.locator(".fs-coord")).toContainText(lat.toFixed(4));
    await expect(dialog.locator(".fs-coord")).toContainText(lon.toFixed(4));
    await expect(dialog.locator(".leaflet-marker-icon")).toBeVisible();
  }
  await dialog.getByRole("button", { name: "完成", exact: true }).click();
  await expect(page.locator(".location-name strong")).toHaveText("地图选点");
  await expect(page.locator(".location-name .coordinates")).toContainText(lat.toFixed(4));
  await expect(page.locator(".location-name .coordinates")).toContainText(lon.toFixed(4));
});

test("zero and missing grid cells keep their focus across provider changes and clear cleanly", async ({ page }) => {
  await fixture(page);
  await mapPage(page);
  await loadGrid(page);
  const dialog = mapDialog(page);
  await expectGridPixels(page);
  await dialog.getByRole("button", { name: "聚焦 2 · 0", exact: true }).click();
  await expectPopup(page, "峰值 0.000");
  await provider(page, "openfreemap");
  await expect(dialog.locator(".fs-map-root")).toHaveAttribute("data-grid-count", "3");
  await expectPopup(page, "峰值 0.000");
  await expectGridPixels(page);
  await dialog.getByRole("button", { name: "聚焦 3 · 缺测", exact: true }).click();
  await expectPopup(page, "缺测 / 计算失败");
  await provider(page, "tianditu");
  await expect(dialog.locator(".fs-map-root")).toHaveAttribute("data-grid-count", "3");
  await expectPopup(page, "缺测 / 计算失败");
  await provider(page, "openfreemap");
  await expect(dialog.locator(".fs-map-root")).toHaveAttribute("data-grid-count", "3");
  await expectPopup(page, "缺测 / 计算失败");
  await expectGridPixels(page);
  await dialog.getByRole("button", { name: "清除网格", exact: true }).click();
  await expect(dialog.locator(".fs-map-root")).toHaveAttribute("data-grid-count", "0");
  await expect(dialog.locator("canvas.firesky-forecast-grid")).toHaveCount(0);
  await expect(dialog.locator(".leaflet-popup-content")).toHaveCount(0);
  await expect(dialog.locator(".leaflet-marker-icon")).toHaveCount(1);
});

test("both map sources fit phone and desktop in both themes with real grid canvas pixels", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await mapPage(page);
  await loadGrid(page);
  for (const source of ["tianditu", "openfreemap"]) {
    await provider(page, source);
    for (const [width, height] of [[390, 667], [768, 720], [1024, 720], [1440, 720]]) {
      await page.setViewportSize({ width, height });
      for (const theme of ["light", "dark"]) {
        await settings(page);
        await page.getByLabel("主题", { exact: true }).selectOption(theme);
        await mapPage(page);
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await expectGridPixels(page);
        const surface = await mapDialog(page).locator(".leaflet-container").evaluate((host) => {
          const rect = host.getBoundingClientRect();
          const workspace = document.querySelector(".workspace");
          return {
            documentOverflow: document.documentElement.scrollWidth > innerWidth,
            workspaceOverflow: workspace.scrollWidth > workspace.clientWidth,
            width: rect.width, height: rect.height,
            inside: rect.left >= 0 && rect.top >= 0 && rect.right <= innerWidth && rect.bottom <= innerHeight,
          };
        });
        expect(surface.documentOverflow).toBe(false);
        expect(surface.workspaceOverflow).toBe(false);
        expect(surface.width).toBeGreaterThan(200);
        expect(surface.height).toBeGreaterThanOrEqual(180);
        expect(surface.inside).toBe(true);
        await page.screenshot({ path: testInfo.outputPath(`${source}-map-${width}-${theme}.png`), fullPage: true });
      }
    }
  }
  expect(errors).toEqual([]);
});

test("selected point and zoom survive repeated light and dark theme changes", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await fixture(page, "mapDebug=1");
  await openFreeStyle(page);
  await provider(page, "openfreemap");
  await expectWebGLPixels(page);
  const dialog = mapDialog(page);
  const surface = dialog.locator(".leaflet-container");
  const pane = dialog.locator(".leaflet-map-pane");
  const scaleSignature = () => dialog.locator(".leaflet-control-scale-line").evaluate((scale) => ({
    text: scale.textContent, width: scale.style.width,
  }));
  const snapshot = () => dialog.evaluate((root) => ({
    coordinates: root.querySelector(".coordinates")?.textContent,
    scale: root.querySelector(".leaflet-control-scale-line")?.textContent,
    scaleWidth: root.querySelector(".leaflet-control-scale-line")?.getAttribute("style") || "",
    marker: root.querySelector(".leaflet-marker-icon")?.getAttribute("style") || "",
  }));
  const box = await surface.boundingBox();
  await surface.click({ position: { x: box.width * .35, y: box.height * .4 } });
  await expect(dialog.locator(".coordinates")).not.toHaveText("30.27410°, 120.15510°");
  const initialScale = await scaleSignature();
  await dialog.locator(".leaflet-control-zoom-in").click();
  await expect.poll(scaleSignature).not.toEqual(initialScale);
  await expect(pane).not.toHaveClass(/leaflet-zoom-anim/);
  const signature = await snapshot();
  expect(signature.coordinates).toMatch(/-?\d+\.\d+°/);
  expect(signature.scale).toBeTruthy();
  for (const theme of ["dark", "light", "dark", "light"]) {
    await dialog.locator(".leaflet-control-zoom-out").click();
    await expect(pane).not.toHaveClass(/leaflet-zoom-anim/);
    await page.waitForTimeout(250);
    // Remove the old GL adapter while its zoom transition callback is pending.
    await dialog.locator(".leaflet-control-zoom-in").click();
    await settings(page);
    await page.getByLabel("主题", { exact: true }).selectOption(theme);
    await mapPage(page);
    await page.waitForTimeout(500);
    const next = await snapshot();
    expect(next.coordinates).toBe(signature.coordinates);
    expect(next.scale).toBe(signature.scale);
    expect(next.scaleWidth).toBe(signature.scaleWidth);
    expect(next.marker).toBe(signature.marker);
  }
  await page.waitForTimeout(700);
  expect(pageErrors).toEqual([]);
});
