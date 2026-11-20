import { test, expect } from "@playwright/test";
import { IN_PAGE_RPC_MOCK } from "./helpers/in-page-rpc.mjs";

async function fixture(page, environmentOverride = false) {
  await page.addInitScript(IN_PAGE_RPC_MOCK);
  await page.addInitScript((overridden) => {
    let id = 0;
    const requests = [];
    let configured = false;
    const status = () => ({
      configured, state: configured ? "configured" : "missing", path: "/fixture/.cdsapirc",
      environment_key: overridden, can_save: !overridden,
    });
    window.__settingsFixture = { requests };
    window.__rpcStatus = () => ({ generation: 1 });
    window.__rpcDirect = (method, params) => {
      requests.push({ method, params });
      if (method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [] };
      if (method === "cams_credentials") return status();
      if (method === "save_cams_key") { configured = true; return status(); }
      if (method === "retention_settings") return { settings: { gfs_cycles: 8, ecmwf_cycles: 4, cams_cycles: 2, satellite_days: 10, report_days: 30, map_days: 14, prune_on_prepare: false } };
      throw new Error(`Unexpected fixture request ${method}`);
    };
    window.__rpcSubmit = () => {};
  }, environmentOverride);
  await page.goto("/?mapOffline=1");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "CAMS · Copernicus ADS", exact: true })).toBeVisible();
}

test("browser settings keep CAMS credential read-only and never write a local key", async ({ page }) => {
  const secret = "fixture-ads-secret";
  const logs = [];
  page.on("console", (message) => logs.push(message.text()));
  page.on("pageerror", (error) => logs.push(error.message));
  await fixture(page);
  await expect(page.locator(".settings-view h2")).toHaveText([
    "外观", "数据源", "缓存与历史保留", "地图", "地图凭据", "CAMS · Copernicus ADS", "Rust 服务",
  ]);
  // 浏览器不渲染凭据写入表单（桌面宿主能力），只保留只读检查与教程。
  await expect(page.getByLabel("ADS Personal Access Token", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存 Key", exact: true })).toHaveCount(0);
  await expect(page.getByText("CAMS 凭据由服务端配置，浏览器不提供凭据写入。修改服务端配置后需重新启动 Rust 服务。", { exact: true })).toBeVisible();
  const exposure = await page.evaluate((token) => ({
    html: document.documentElement.outerHTML.includes(token),
    inputs: [...document.querySelectorAll("input")].some((input) => input.value.includes(token)),
    local: JSON.stringify(localStorage).includes(token),
    session: JSON.stringify(sessionStorage).includes(token),
    saves: window.__settingsFixture.requests.filter((request) => request.method === "save_cams_key").length,
  }), secret);
  expect(exposure).toEqual({ html: false, inputs: false, local: false, session: false, saves: 0 });
  expect(logs.some((line) => line.includes(secret))).toBe(false);
});

test("CAMS environment overrides keep the browser read-only notice and never write a key", async ({ page }) => {
  await fixture(page, true);
  await expect(page.getByText("CAMS 凭据由服务端配置，浏览器不提供凭据写入。修改服务端配置后需重新启动 Rust 服务。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("ADS Personal Access Token", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存 Key", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => window.__settingsFixture.requests.some((request) => request.method === "save_cams_key"))).toBe(false);
});

test("settings keep readable native groups within narrow and wide light and dark windows", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await expect(page.getByLabel("地图服务", { exact: true })).toHaveValue("tianditu");
  await expect(page.getByLabel(/Apple Maps/)).toHaveCount(0);
  for (const [width, height] of [[390, 667], [768, 720], [1024, 720], [1440, 720]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ["light", "dark"]) {
      await page.getByLabel("主题", { exact: true }).selectOption(theme);
      await page.evaluate(async () => {
        document.querySelector(".workspace").scrollTop = 0;
        await document.fonts.ready;
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      const bounds = await page.evaluate(() => ({
        document: document.documentElement.scrollWidth > innerWidth,
        workspace: document.querySelector(".workspace").scrollWidth > document.querySelector(".workspace").clientWidth,
        clipped: [...document.querySelectorAll(".settings-view input,.settings-view select,.settings-view button")].some((element) => {
          const rect = element.getBoundingClientRect();
          return rect.width > 0 && (rect.left < 0 || rect.right > innerWidth + 1);
        }),
      }));
      expect(bounds).toEqual({ document: false, workspace: false, clipped: false });
      await page.screenshot({ path: testInfo.outputPath(`settings-${width}-${theme}.png`), fullPage: true, animations: "disabled", caret: "hide" });
    }
  }
  expect(errors).toEqual([]);
});

test("elevation source defaults to auto, persists the choice and rejects corrupt stored values", async ({ page }) => {
  await fixture(page);
  const select = page.getByLabel("海拔来源", { exact: true });
  await expect(select).toHaveValue("auto");
  await expect(select.locator("option")).toHaveText(["自动（在线优先）", "HTTP 在线查询", "SRTM 本地地形"]);
  await expect(page.getByText("自动：优先 open-meteo 在线查询，失败时自动回退本地时区 + SRTM 地形瓦片。", { exact: true })).toBeVisible();
  await select.selectOption("http");
  await expect(page.getByText("HTTP：仅通过 open-meteo 在线查询海拔与时区；查询失败直接报错，不回退本地地形。", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("firesky.elevationSource"))).toBe("http");
  await page.reload();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByLabel("海拔来源", { exact: true })).toHaveValue("http");
  await page.evaluate(() => localStorage.setItem("firesky.elevationSource", "bogus"));
  await page.reload();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByLabel("海拔来源", { exact: true })).toHaveValue("auto");
});
