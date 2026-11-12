import { test, expect } from "@playwright/test";

async function fixture(page, environmentOverride = false) {
  await page.addInitScript((overridden) => {
    let id = 0;
    const requests = [];
    let configured = false;
    const status = () => ({
      configured, state: configured ? "configured" : "missing", path: "/fixture/.cdsapirc",
      environment_key: overridden, can_save: !overridden,
    });
    window.__settingsFixture = { requests };
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
    window.isTauri = true;
    window.__TAURI_INTERNALS__ = {
      metadata: { currentWindow: { label: "main" } },
      transformCallback() { return ++id; },
      async invoke(command, args = {}) {
        if (command === "plugin:event|listen") return ++id;
        if (command === "plugin:event|unlisten") return;
        if (command === "rpc_status") return {
          running: true, generation: 1, workspace: "/fixture", engineBinary: "/fixture/firesky-engine",
        };
        if (command === "rpc_request") {
          requests.push({ method: args.method, params: args.params });
          if (args.method === "hello") return { protocol: 1, engine: "firesky-rust", workspace: "/fixture", regions: [] };
          if (args.method === "cams_credentials") return status();
          if (args.method === "save_cams_key") {
            configured = true;
            return status();
          }
        }
        throw new Error(`Unexpected fixture command ${command}`);
      },
    };
  }, environmentOverride);
  await page.goto("/?mapOffline=1");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "CAMS · Copernicus ADS", exact: true })).toBeVisible();
}

test("Swift settings groups preserve explicit CAMS saving and clear the submitted draft", async ({ page }) => {
  const secret = "fixture-ads-secret";
  const logs = [];
  page.on("console", (message) => logs.push(message.text()));
  page.on("pageerror", (error) => logs.push(error.message));
  await fixture(page);
  await expect(page.locator(".settings-view h2")).toHaveText([
    "外观", "数据源", "地图", "地图凭据", "CAMS · Copernicus ADS", "本地引擎",
  ]);
  await expect(page.getByLabel("ADS Personal Access Token", { exact: true })).toBeEnabled();
  expect(await page.evaluate(() => window.__settingsFixture.requests.some((request) => request.method === "save_cams_key"))).toBe(false);
  await page.getByLabel("ADS Personal Access Token", { exact: true }).fill(secret);
  await page.getByRole("button", { name: "保存 Key", exact: true }).click();
  await expect(page.getByText("CAMS Key 已保存到本机配置；有效性与数据集授权在实际下载时验证。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("ADS Personal Access Token", { exact: true })).toHaveValue("");
  const exposure = await page.evaluate((token) => ({
    html: document.documentElement.outerHTML.includes(token),
    inputs: [...document.querySelectorAll("input")].some((input) => input.value.includes(token)),
    local: JSON.stringify(localStorage).includes(token),
    session: JSON.stringify(sessionStorage).includes(token),
    saves: window.__settingsFixture.requests.filter((request) => request.method === "save_cams_key").length,
  }), secret);
  expect(exposure).toEqual({ html: false, inputs: false, local: false, session: false, saves: 1 });
  expect(logs.some((line) => line.includes(secret))).toBe(false);
});

test("CAMS environment overrides disable editing and never write a local key", async ({ page }) => {
  await fixture(page, true);
  await expect(page.getByText("环境变量正在覆盖配置。修改 CDSAPI_KEY / CDSAPI_URL 后需重新启动应用。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("ADS Personal Access Token", { exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "保存 Key", exact: true })).toBeDisabled();
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
