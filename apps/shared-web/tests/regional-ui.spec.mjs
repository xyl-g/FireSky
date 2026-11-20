import { test, expect } from "@playwright/test";
import { IN_PAGE_RPC_MOCK } from "./helpers/in-page-rpc.mjs";

async function fixture(page) {
  await page.addInitScript(IN_PAGE_RPC_MOCK);
  await page.addInitScript(() => {
    const tickets = new Map();
    const requests = [];
    const raw = (values) => btoa(String.fromCharCode(...values));
    const u8 = (values) => raw(values.map((value) => Math.max(0, Math.min(255, Math.round(value)))));
    const u16 = (values) => raw(values.flatMap((value) => {
      const encoded = Math.max(0, Math.min(65535, Math.round(value)));
      return [encoded & 255, encoded >> 8];
    }));
    const frameData = {
      version: 103, encoding: "base64-quantized-row-major-v1", point_count: 3,
      frames: Array.from({ length: 20 }, (_, index) => ({
        offset_minutes: -20 + index * 5,
        valid: u8([1, 1, 1]),
        instant_quality_index: u16([index * 100, 250 - index * 20, 0]),
        burnable_cloud_coverage: u8([128, 200, 0]),
        forward_view_cloud_field_amount: u8([200, 180, 0]),
        forward_burnable_cloud_field_amount: u8([90, 150, 0]),
        forward_burnable_cloud_ratio: u8([112, 210, 0]),
        vividness_score: u8([100, 140, 0]),
        solar_incoming_block_risk: u8([12, 30, 0]),
        observer_return_transmission: u8([220, 180, 0]),
        local_aod550: u16([400 + index, 700 + index, 0]),
      })),
    };
    const profileSource = {
      contract: "rust-desktop-profile-source-v1", available: true,
      result_id: "a".repeat(64), source_identity: { bytes: 256, sha256: "b".repeat(64) },
      model_source: "gfs", cycle_utc: "2026-11-11T00:00:00Z",
      query_method: "profile_query", profile_kind: "model-pressure-cross-section",
    };
    const report = (ready) => ({
      ready, datasets: ["gfs", "ecmwf", "cams"].map((source) => ({
        source, ready, missing_count: ready ? 0 : 1, verified: ready, is_latest: ready,
        request_cycle: "2026-11-11 00Z", latest_available_cycle: ready ? "2026-11-11 00Z" : "未核验",
      })),
    });
    const result = {
      model_results: {
        gfs: {
          cycle_time_utc: "2026-11-11T00:00:00Z", point_count: 3, error_count: 1, frame_data: frameData,
          profile_source: profileSource,
          precision: { point_spacing_deg: .25, model_grid_spacing_deg: .25, vertical_profiles: true, full_point_coverage: true, aerosol_profile_mode: "cams-full-profile" },
          input_manifest: { sources: [{ role: "cams:2026-11-11T09:00:00Z" }] },
          points: [
            { lat: 30.25, lon: 120, frame_index: 0, anchor_local: "2026-11-11T17:00:00.123456+08:00",
              timezone_name: "Asia/Shanghai", best_time_local: "2026-11-11T17:05:00.123456+08:00",
              peak_quality_index: 0, quality_index: 0, dominant_burnable_pressure_family: null,
              burnable_cloud_coverage_effective: 0, burnable_duration_minutes: 0, quality_aod550: 0,
              incoming_aod_adjustment: .125, observer_aod_adjustment: -.025, confidence_score: 0,
            },
            { lat: 30.25, lon: 120.25, frame_index: 1, anchor_local: "2026-11-11T17:00:00.123456+08:00",
              timezone_name: "Asia/Shanghai", peak_quality_index: 1.75, best_time_local: "2026-11-11T17:10:00.123456+08:00" },
            { lat: 30.25, lon: 120.5, peak_quality_index: null },
          ],
          errors: [{ lat: 30.25, lon: 120.5, error: "fixture missing grid" }],
        },
      },
      model_errors: { ec: "fixture EC unavailable" },
    };
    window.__regionalFixture = { requests };
    localStorage.setItem("firesky.appearance", "light");
    window.__rpcStatus = () => ({ generation: 1 });
    window.__rpcDirect = (method) => {
      if (method === "hello") return {
        protocol: 1, engine: "firesky-rust", workspace: "/fixture",
        regions: [{ code: "east-asia", label: "东亚" }, { code: "china", label: "中国" }],
      };
      if (method === "cams_credentials") return { configured: false, can_save: true };
      if (method === "dismiss") return { dismissed: true };
      if (method === "publications") return { session: "regional-session", report: report(true) };
      if (method === "retention_settings") return { settings: { prune_on_prepare: false } };
      return undefined;
    };
    window.__rpcSubmit = (job) => {
      requests.push({ method: job.method, params: job.params });
      tickets.set(job.id, job);
      if (job.method === "check") {
        job.resolve({ session: "regional-session", report: report(false) });
      } else if (job.method === "prepare") {
        job.resolve({ session: "regional-session", report: report(true) });
        job.progress({ percent: 100, message: "fixture preparation complete" });
      } else if (job.method === "forecast") {
        if (job.params.confirmed !== true || job.params.session !== "regional-session") {
          throw new Error("Forecast must use the prepared token and explicit confirmation");
        }
        job.resolve(result);
      } else if (job.method === "profile_query") {
        job.resolve({
          ...job.params, contract: "rust-desktop-profile-query-v1", cycle_utc: "2026-11-11T00:00:00Z",
          status: "ready", profile_kind: "model-pressure-cross-section",
          cross_section: {
            model_source: job.params.model_source, solar_azimuth_deg: 270, solar_elevation_deg: -1,
            offset_minutes: 5, distances_km: [0], pressure_levels_hpa: [850],
            terrain_elevation_asl_m: [0], required_illumination_base_asl_m: [1000],
            fields: { geopotential_height_asl_m: [[1000]], cloud_fraction_percent: [[0]] },
          },
        });
      } else throw new Error(`Unexpected fixture method ${job.method}`);
    };
    window.__rpcCancelJob = () => false;
  });
  await page.route(/https:\/\/(?:[^/]+\.)?(?:openstreetmap\.org|tianditu\.gov\.cn|arcgisonline\.com)\//, (route) => route.abort());
  await page.goto("/?mapOffline=1");
  await expect(page.locator(".engine-badge.ok")).toContainText("引擎已连接");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByLabel("地图服务", { exact: true }).selectOption("tianditu");
  await page.getByRole("button", { name: "区域预测", exact: true }).click();
  await expect(page.getByRole("heading", { name: "区域预测", exact: true })).toBeVisible();
}

async function predict(page) {
  await page.getByRole("button", { name: "开始预测", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "确认预测", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "下载缺失数据", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "确认预测", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => window.__regionalFixture.requests.some((call) => call.method === "forecast"))).toBe(false);
  await dialog.getByRole("button", { name: "确认预测", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('.model-map[aria-label="GFS 区域地图"] .fs-map-root')).toHaveAttribute("data-grid-count", "3");
}

test("regional preparation explicitly confirms a partial model result and keeps all zero/missing cells", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await predict(page);
  const methods = await page.evaluate(() => window.__regionalFixture.requests
    .filter((call) => ["check", "prepare", "forecast"].includes(call.method)));
  expect(methods.map((call) => call.method)).toEqual(["check", "prepare", "forecast"]);
  expect(methods[0].params.kind).toBe("regional");
  // 区域单模型契约（引擎拒绝 both）：默认 GFS。
  expect(methods[0].params.payload).toMatchObject({ regions: ["east-asia"], model_source: "gfs", spacing_deg: .25 });
  expect(methods[2].params).toEqual({ session: "regional-session", confirmed: true });
  await expect(page.locator('[aria-label="GFS 区域摘要"]')).toContainText("预测完成");
  await expect(page.locator('[aria-label="GFS 区域摘要"]')).toContainText("2 有效 · 1 高值");
  await expect(page.locator('[aria-label="GFS 区域摘要"]')).toContainText("1 缺测");
  await expect(page.locator('[aria-label="EC 区域摘要"]')).toContainText("未完成");

  const gridCanvas = page.locator('.model-map[aria-label="GFS 区域地图"] .firesky-forecast-grid');
  const painted = await gridCanvas.evaluate((canvas) => {
    const { data, width, height } = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
    let zero = false, high = false, missing = false, xSum = 0, ySum = 0, missingCount = 0;
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4, r = data[i], g = data[i + 1], b = data[i + 2], alpha = data[i + 3];
      if (alpha >= 150 && r > 120 && r < 150 && g > 130 && g < 160 && b > 145 && b < 175) zero = true;
      if (alpha >= 150 && r > 180 && g < 70 && b < 70) high = true;
      if (alpha >= 50 && alpha < 80 && r >= 80 && r < 110 && g >= 80 && g < 120 && b >= 90 && b < 130) {
        missing = true; xSum += x; ySum += y; missingCount++;
      }
    }
    return { zero, high, missing, missingX: xSum / missingCount / devicePixelRatio, missingY: ySum / missingCount / devicePixelRatio };
  });
  expect(painted).toMatchObject({ zero: true, high: true, missing: true });
  await page.locator('.model-map[aria-label="GFS 区域地图"] .leaflet-container').click({ position: { x: painted.missingX, y: painted.missingY } });
  await expect(page.locator(".cell-inspection .cell-reading").first()).toContainText("缺测");
  await expect(page.locator(".cell-inspection .detail-list dd")).toHaveText(Array(16).fill("--"));
  await expect(page.locator(".leaflet-popup-content")).toHaveCount(1);
  await expect(page.locator(".leaflet-popup-content")).toContainText("缺测 / 计算失败");

  const ranking = page.locator(".ranking-list .ranking-row");
  await expect(ranking).toHaveCount(2);
  await expect(ranking.first()).toContainText("1.75");
  await expect(ranking.last()).toContainText("0.00");
  await ranking.last().click();
  await expect(page.locator(".cell-inspection .coordinate")).toHaveText("30.2500°, 120.0000°");
  await expect(page.locator(".cell-inspection .cell-reading").first().locator("b")).toHaveText("0.00");
  await expect(page.locator(".cell-inspection .cell-reading").last()).toContainText("缺测");
  const gfsDiagnostics = page.getByLabel("GFS 网格诊断", { exact: true });
  await expect(gfsDiagnostics).toContainText("事件窗口质量0.000");
  await expect(gfsDiagnostics).toContainText("主导可烧压力族无可烧目标云");
  await expect(gfsDiagnostics).toContainText("有效可烧云0.0%");
  await expect(gfsDiagnostics).toContainText("可烧时长0 分钟");
  await expect(gfsDiagnostics).toContainText("质量 AOD0.000");
  await expect(gfsDiagnostics).toContainText("入射修正+0.125");
  await expect(gfsDiagnostics).toContainText("观测修正-0.025");
  await expect(gfsDiagnostics).toContainText("置信度0%");
  await expect(page.getByLabel("EC 网格诊断", { exact: true }).locator("dd")).toHaveText(Array(8).fill("--"));
  await page.locator(".report-details summary").click();
  const provenance = page.getByLabel("GFS 数据来源与精度", { exact: true });
  await expect(provenance).toContainText("CAMS 起报 UTC未提供");
  await expect(provenance).toContainText("CAMS 有效时次 UTC2026-11-11 09:00:00Z");
  await expect(provenance).toContainText("采样网格0.25° × 0.25°");
  await expect(provenance).toContainText("垂直剖面是");
  await expect(page.locator(".leaflet-popup-content")).toHaveCount(1);
  await expect(page.locator(".leaflet-popup-content")).toContainText("峰值 0.000");
  await ranking.first().click();
  await expect(page.locator(".cell-inspection .cell-reading").first().locator("b")).toHaveText("1.75");
  await expect(page.locator(".leaflet-popup-content")).toHaveCount(1);
  await expect(page.locator(".leaflet-popup-content")).toContainText("峰值 1.750");

  await page.getByRole("checkbox", { name: "显示网格", exact: true }).uncheck();
  await expect(page.locator(".model-map .fs-map-root")).toHaveAttribute("data-grid-count", "0");
  await page.getByRole("checkbox", { name: "显示网格", exact: true }).check();
  await expect(page.locator(".model-map .fs-map-root")).toHaveAttribute("data-grid-count", "3");

  const modes = page.getByRole("group", { name: "区域地图模式", exact: true });
  await modes.getByRole("button", { name: "EC", exact: true }).click();
  await expect(page.locator('.model-map[aria-label="EC 区域地图"] .fs-map-root')).toHaveAttribute("data-grid-count", "0");
  await expect(page.locator(".model-map .map-message.error")).toHaveText("fixture EC unavailable");
  await modes.getByRole("button", { name: "双图对照", exact: true }).click();
  await expect(page.locator(".model-map")).toHaveCount(2);
  await expect(page.locator('.model-map[aria-label="GFS 区域地图"] .fs-map-root')).toHaveAttribute("data-grid-count", "3");
  await expect(page.locator('.model-map[aria-label="EC 区域地图"] .fs-map-root')).toHaveAttribute("data-grid-count", "0");
  await page.getByRole("group", { name: "详情与排行模式", exact: true }).getByRole("button", { name: "GFS", exact: true }).click();
  await page.getByRole("searchbox", { name: "搜索网格", exact: true }).fill("120.2500");
  await expect(ranking).toHaveCount(1);
  await ranking.first().click();
  await expect(page.locator(".cell-inspection .cell-reading").first().locator("b")).toHaveText("1.75");
  await page.getByRole("button", { name: "适应预测范围", exact: true }).click();
  await page.getByRole("button", { name: "网格详情与排行", exact: true }).click();
  await expect(page.locator(".inspector")).toHaveCount(0);
  await page.getByRole("button", { name: "网格详情与排行", exact: true }).click();
  await expect(page.locator(".inspector")).toBeVisible();
  await expect(page.getByLabel("地图服务", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("group", { name: "地图源", exact: true })).toHaveCount(0);
  await expect(page.getByRole("group", { name: "底图类型", exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("selected regional grid shares precise UTC across trend, ledger, and dynamic profile", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await predict(page);
  await page.locator(".ranking-row").last().click();
  await expect(page.locator(".cell-inspection .coordinate")).toHaveText("30.2500°, 120.0000°");

  const selectedTime = page.getByLabel("网格结果时次", { exact: true });
  await expect(selectedTime).toHaveValue("2026-11-11T09:05:00.123456Z");
  await page.getByRole("tab", { name: "趋势", exact: true }).click();
  await expect(page.getByRole("img", { name: "本地 AOD550 逐时光学厚度曲线", exact: true })).toBeVisible();
  const localAod = page.locator(".detail-panel .readings > div")
    .filter({ has: page.locator("dt", { hasText: /^本地 AOD550$/ }) }).locator("dd");
  await expect(localAod).toHaveText("0.405");
  await expect(selectedTime).toHaveValue("2026-11-11T09:05:00.123456Z");

  await page.getByRole("tab", { name: "逐时", exact: true }).click();
  const ledger = page.locator(".ledger-scroll");
  await expect(ledger.locator("thead")).toContainText("本地 AOD550");
  await expect(ledger.locator("thead")).not.toContainText("有效 AOD");
  const best = ledger.locator("tr.best");
  await expect(best).toHaveCount(1);
  await expect(best.locator("button")).toContainText("最佳");
  await expect(best).toContainText("0.405");

  await page.getByRole("tab", { name: "剖面", exact: true }).click();
  await expect(page.getByRole("img", { name: "沿太阳方向的模式云量剖面", exact: true })).toBeVisible();
  const query = await page.evaluate(() => window.__regionalFixture.requests.find((call) => call.method === "profile_query"));
  expect(query.params).toMatchObject({
    model_source: "gfs", lat: 30.25, lon: 120,
    valid_time_utc: "2026-11-11T09:05:00.123456Z",
  });
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 820 });
    await page.locator(".profile").scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`regional-selected-profile-${width}.png`), fullPage: true });
  }
  expect(errors).toEqual([]);
});

test("regional maps and inspector fit responsive light and dark workspaces", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await predict(page);
  await page.getByRole("group", { name: "区域地图模式", exact: true }).getByRole("button", { name: "双图对照", exact: true }).click();
  for (const [width, height] of [[390, 667], [768, 720], [1024, 720], [1440, 720]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ["light", "dark"]) {
      await page.getByRole("button", { name: "设置", exact: true }).click();
      await page.getByLabel("主题", { exact: true }).selectOption(theme);
      await page.getByRole("button", { name: "区域预测", exact: true }).click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.locator('.model-map[aria-label="GFS 区域地图"] .fs-map-root')).toHaveAttribute("data-grid-count", "3");
      await expect(page.locator(".model-map")).toHaveCount(2);
      await page.locator(".ranking-row").last().click();
      await page.locator(".report-details").evaluateAll((elements) => elements.forEach((element) => { element.open = true; }));
      await expect(page.getByLabel("GFS 网格诊断", { exact: true })).toContainText("入射修正+0.125");
      const sizes = await page.evaluate(() => ({
        documentOverflow: document.documentElement.scrollWidth > innerWidth,
        workspaceOverflow: document.querySelector(".workspace").scrollWidth > document.querySelector(".workspace").clientWidth,
        resultWidth: document.querySelector(".regional-results").getBoundingClientRect().width,
        toolbarWidth: document.querySelector(".regional-toolbar").getBoundingClientRect().width,
        maps: [...document.querySelectorAll(".regional-results .map-surface")].map((element) => {
          const rect = element.getBoundingClientRect();
          return { width: rect.width, height: rect.height };
        }),
      }));
      expect(sizes.documentOverflow).toBe(false);
      expect(sizes.workspaceOverflow).toBe(false);
      expect(Math.abs(sizes.resultWidth - sizes.toolbarWidth)).toBeLessThanOrEqual(1);
      expect(sizes.maps.every((map) => map.width > 100 && map.height >= 280)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`regional-${width}-${theme}.png`), fullPage: true });
    }
  }
  expect(errors).toEqual([]);
});

test("regional multi-selection validates empty and reversed ranges before sending an exact custom payload", async ({ page }) => {
  await fixture(page);
  const menu = page.locator("details.region-menu");
  const start = page.getByRole("button", { name: "开始预测", exact: true });
  const checks = () => page.evaluate(() => window.__regionalFixture.requests.filter((call) => call.method === "check"));
  await menu.locator("summary").click();
  await menu.getByRole("checkbox", { name: "东亚", exact: true }).uncheck();
  await menu.locator("summary").click();
  await start.click();
  await expect(page.getByText("请至少选择一个预测区域。", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "预测准备", exact: true })).not.toBeVisible();
  expect(await checks()).toEqual([]);

  await menu.locator("summary").click();
  await menu.getByRole("checkbox", { name: "中国", exact: true }).check();
  await menu.getByRole("checkbox", { name: "自定义范围", exact: true }).check();
  await menu.locator("summary").click();
  await page.getByRole("button", { name: "编辑区域坐标", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "启用自定义范围", exact: true })).toBeChecked();
  await page.getByRole("spinbutton", { name: "西界", exact: true }).fill("125");
  await page.getByRole("spinbutton", { name: "东界", exact: true }).fill("120");
  await page.getByRole("spinbutton", { name: "南界", exact: true }).fill("32");
  await page.getByRole("spinbutton", { name: "北界", exact: true }).fill("31");
  await start.click();
  await expect(page.getByText("请检查区域边界：经度须在 ±180° 内、纬度须在 ±90° 内，西界不得等于东界且北界须大于南界。", { exact: true })).toBeVisible();
  expect(await checks()).toEqual([]);

  await page.getByRole("spinbutton", { name: "西界", exact: true }).fill("110.25");
  await page.getByRole("spinbutton", { name: "东界", exact: true }).fill("111.5");
  await page.getByRole("spinbutton", { name: "南界", exact: true }).fill("0");
  await page.getByRole("spinbutton", { name: "北界", exact: true }).fill("1.25");
  await page.getByRole("textbox", { name: "区域时区", exact: true }).fill("Etc/UTC");
  await page.getByLabel("区域目标日期", { exact: true }).fill("2026-11-13");
  await page.getByRole("group", { name: "观测现象", exact: true }).getByRole("button", { name: "朝霞", exact: true }).click();
  await start.click();
  const dialog = page.getByRole("dialog", { name: "预测准备", exact: true });
  await expect(dialog).toBeVisible();
  await expect.poll(checks).toHaveLength(1);
  const sent = await checks();
  expect(sent).toHaveLength(1);
  expect(sent[0].params).toMatchObject({
    kind: "regional",
    payload: {
      regions: ["china", "custom"],
      custom_bounds: { west: 110.25, east: 111.5, south: 0, north: 1.25 },
      custom_timezone_name: "Etc/UTC", timezone_name: "Etc/UTC",
      target_date: "2026-11-13", phenomenon: "dawn", spacing_deg: .25,
    },
  });
  await dialog.getByRole("button", { name: "返回", exact: true }).click();
  expect(await page.evaluate(() => window.__regionalFixture.requests.some((call) => ["prepare", "forecast"].includes(call.method)))).toBe(false);
});
